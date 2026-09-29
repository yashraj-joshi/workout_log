"""ApiFunction: every route that does not touch OpenAI.

This function has no permission to read the OpenAI key, so even a bug here
cannot leak it. The AI routes live in assistant_app.py on a separate function.
"""

from __future__ import annotations

import json
import logging
import os
import time

from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from mangum import Mangum

from . import errors, service
from .auth import User, current_user, git_commit
from .errors import ApiError
from .models import DayPatch, ExerciseIn, MoveIn, valid_date, valid_key
from .repo import MAX_PAGE, Repo

log = logging.getLogger("workoutlog")
logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))

app = FastAPI(title="Workout Log API", docs_url=None, redoc_url=None, openapi_url=None)
errors.install(app)

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
def add_exercise(exercise: ExerciseIn, date: str = Depends(day_date),
                 user: User = Depends(current_user), repo: Repo = Depends(get_repo)):
    return {"day": service.add_exercise(repo, user.sub, date, exercise)}


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
def move_exercise(body: MoveIn, date: str = Depends(day_date),
                  key: str = Depends(exercise_key), user: User = Depends(current_user),
                  repo: Repo = Depends(get_repo)):
    return {"day": service.move_exercise(repo, user.sub, date, key, body.toDate)}


handler = Mangum(app, lifespan="off")
