"""ApiFunction: every route that does not touch OpenAI.

This function has no permission to read the OpenAI key, so even a bug here
cannot leak it. The AI routes live in assistant_app.py on a separate function.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Callable

from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from mangum import Mangum

from . import errors, service, sessions
from .auth import User, current_user, git_commit
from .errors import ApiError
from .models import DayPatch, ExerciseIn, MoveIn, valid_date, valid_key
from .repo import MAX_PAGE, Repo, RequestRecord, RequestReplayed

log = logging.getLogger("workoutlog")
logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))

app = FastAPI(title="Workout Log API", docs_url=None, redoc_url=None, openapi_url=None)
errors.install(app)
app.include_router(sessions.router)

_repo: Repo | None = None


def get_repo() -> Repo:
    """One Repo per container, so the boto3 client is reused across invocations."""
    global _repo
    if _repo is None:
        _repo = Repo()
    return _repo


def day_date(date: str = Path(..., min_length=10, max_length=10)) -> str:
    try:
        return valid_date(date)
    except ValueError as exc:
        raise ApiError(400, "validation_error", str(exc)) from exc


def exercise_key(key: str = Path(..., min_length=2, max_length=2)) -> str:
    try:
        return valid_key(key)
    except ValueError as exc:
        raise ApiError(400, "validation_error", str(exc)) from exc


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _valid_uuid(value: str, what: str) -> str:
    if not _UUID_RE.match(value):
        raise ApiError(400, "validation_error", f"{what} should be a UUID.")
    return value.lower()


def idempotency_key(
    value: str | None = Header(default=None, alias="Idempotency-Key", max_length=64),
) -> str | None:
    """Optional. The app creates one per action and sends it unchanged on every
    retry of that action, so a reply lost on a weak connection can't log twice."""
    return None if value is None else _valid_uuid(value.strip(), "Idempotency-Key")


def _request_hash(request: Request, body) -> str:
    """What was asked, so a key reused for a different request is caught. The
    validated body, not the raw bytes: key order and spacing don't matter."""
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{request.method} {request.url.path}\n{canonical}".encode()).hexdigest()


def _replay(stored: dict, request_hash: str) -> JSONResponse:
    if stored.get("requestHash") != request_hash:
        raise ApiError(422, "idempotency_key_reused",
                       "That Idempotency-Key was already used for a different request.")
    if stored.get("status") != "done":
        raise ApiError(409, "request_in_progress", "That request is still running.",
                       headers={"Retry-After": "2"})
    return JSONResponse(content=stored.get("response") or {}, status_code=int(stored["httpStatus"]))


def _idempotent(repo: Repo, sub: str, key: str | None, request: Request, body, status: int,
                run: Callable[[RequestRecord | None], dict]):
    """Run a single-transaction write at most once per Idempotency-Key. A
    repeat gets the first response back: no write, no dataVersion bump."""
    if key is None:
        return run(None)
    record = RequestRecord(key, _request_hash(request, body), status)
    stored = repo.get_request(sub, key)
    if stored:
        return _replay(stored, record.request_hash)
    try:
        return run(record)
    except RequestReplayed:
        # Another copy of this request committed between our check and our write.
        return _replay(repo.get_request(sub, key) or {}, record.request_hash)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """Structured one-liner per request. Deliberately carries no body: request
    bodies hold notes, summaries and transcripts."""
    started = time.monotonic()
    response = await call_next(request)
    event = request.scope.get("aws.event") or {}
    context = (event.get("requestContext") or {})
    claims = ((context.get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    log.info(json.dumps({
        "requestId": context.get("requestId"),
        "route": f"{request.method} {request.scope.get('route').path if request.scope.get('route') else request.url.path}",
        "status": response.status_code,
        "ms": round((time.monotonic() - started) * 1000, 1),
        "sub": claims.get("sub"),
    }))
    return response


# --------------------------------------------------------------------------

@app.get("/health")
def health():
    """No auth: CloudFront and the smoke test need a route that always answers."""
    return {"ok": True, "commit": git_commit()}


@app.get("/v1/me")
def me(user: User = Depends(current_user), repo: Repo = Depends(get_repo)):
    repo.ensure_profile(user.sub, user.email)
    return {"email": user.email, "groups": list(user.groups), "canUseAI": user.can_use_ai}


def _etag_matches(header: str | None, version: int) -> bool:
    if not header:
        return False
    for candidate in header.split(","):
        cleaned = candidate.strip().removeprefix("W/").strip().strip('"')
        if cleaned == str(version):
            return True
    return False


@app.get("/v1/days")
def list_days(
    response: Response,
    user: User = Depends(current_user),
    repo: Repo = Depends(get_repo),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=MAX_PAGE, ge=1, le=MAX_PAGE),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
):
    """The ETag is the profile's dataVersion, so an unchanged log costs one
    GetItem and returns 304. This is the route the app polls."""
    version = repo.data_version(user.sub)
    etag = f'"{version}"'
    # A cursor is a continuation of a page set; revalidating it makes no sense.
    if cursor is None and _etag_matches(if_none_match, version):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-store"})
    page = repo.list_days(user.sub, cursor=cursor, limit=limit)
    return JSONResponse(
        content={**page, "dataVersion": version},
        headers={"ETag": etag, "Cache-Control": "no-store"},
    )


@app.get("/v1/days/{date}")
def read_day(date: str = Depends(day_date), user: User = Depends(current_user),
             repo: Repo = Depends(get_repo)):
    return {"day": service.get_day(repo, user.sub, date)}


@app.patch("/v1/days/{date}")
def patch_day(patch: DayPatch, date: str = Depends(day_date),
              user: User = Depends(current_user), repo: Repo = Depends(get_repo)):
    return {"day": service.patch_day(repo, user.sub, date, patch)}


@app.post("/v1/days/{date}/exercises", status_code=201)
def add_exercise(exercise: ExerciseIn, request: Request, date: str = Depends(day_date),
                 user: User = Depends(current_user), repo: Repo = Depends(get_repo),
                 key: str | None = Depends(idempotency_key)):
    return _idempotent(repo, user.sub, key, request, exercise, 201, lambda record: {
        "day": service.add_exercise(repo, user.sub, date, exercise, request=record)})


@app.put("/v1/days/{date}/exercises/{key}")
def put_exercise(exercise: ExerciseIn, date: str = Depends(day_date),
                 key: str = Depends(exercise_key), user: User = Depends(current_user),
                 repo: Repo = Depends(get_repo)):
    return {"day": service.replace_exercise(repo, user.sub, date, key, exercise)}


@app.delete("/v1/days/{date}/exercises/{key}")
def delete_exercise(date: str = Depends(day_date), key: str = Depends(exercise_key),
                    user: User = Depends(current_user), repo: Repo = Depends(get_repo)):
    return {"day": service.remove_exercise(repo, user.sub, date, key)}


@app.post("/v1/days/{date}/exercises/{key}/move")
def move_exercise(body: MoveIn, request: Request, date: str = Depends(day_date),
                  key: str = Depends(exercise_key), user: User = Depends(current_user),
                  repo: Repo = Depends(get_repo),
                  idem_key: str | None = Depends(idempotency_key)):
    return _idempotent(repo, user.sub, idem_key, request, body, 200, lambda record: {
        "day": service.move_exercise(repo, user.sub, date, key, body.toDate, request=record)})


@app.get("/v1/requests/{key}")
def read_request(key: str = Path(..., min_length=36, max_length=36),
                 user: User = Depends(current_user), repo: Repo = Depends(get_repo)):
    """A few bytes, so after a dropped upload the app can ask whether the
    server already has the request before sending it again."""
    stored = repo.get_request(user.sub, _valid_uuid(key, "Request key"))
    if stored is None:
        raise ApiError(404, "not_found", "No request with that key.")
    return {"state": stored.get("status")}


handler = Mangum(app, lifespan="off")
