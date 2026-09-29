"""AssistantFunction: the routes that spend OpenAI credit.

Split from ApiFunction for one reason: this is the only function with
ssm:GetParameter on the OpenAI key, and the only one that imports the SDK.

Phase 1 ships POST /v1/days/{date}/finish. POST /v1/assistant and
/v1/assistant/undo land in phase 6.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Path, Request
from mangum import Mangum

from . import errors, summary
from .auth import User, current_user, git_commit, require_ai
from .errors import ApiError
from .models import FinishIn, valid_date
from .repo import Repo

log = logging.getLogger("workoutlog")
logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))

app = FastAPI(title="Workout Log Assistant", docs_url=None, redoc_url=None, openapi_url=None)
errors.install(app)

_repo: Repo | None = None


def get_repo() -> Repo:
    global _repo
    if _repo is None:
        _repo = Repo()
    return _repo


def get_summarizer():
    """Injectable so tests never reach OpenAI. Imported lazily: importing the
    SDK costs cold-start time on requests that turn out to be 403 or 429."""
    from .assistant.summarizer import write_summary
    return write_summary


def day_date(date: str = Path(..., min_length=10, max_length=10)) -> str:
    try:
        return valid_date(date)
    except ValueError as exc:
        raise ApiError(400, "validation_error", str(exc)) from exc


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def guard_ai(repo: Repo, user: User) -> int:
    """Group check, then an atomic counter so parallel calls can't both slip
    under the cap. The window is the UTC day, which is what the error says."""
    require_ai(user)
    limit = int(os.environ.get("DAILY_AI_LIMIT", "100"))
    used = repo.bump_usage(user.sub, utc_today())
    if used > limit:
        raise ApiError(429, "daily_limit",
                       f"Daily AI limit of {limit} reached. It resets at midnight UTC.")
    return used


@app.middleware("http")
async def access_log(request: Request, call_next):
    """Runs around every request and, once it finishes, logs one JSON line to
    CloudWatch: requestId, method and path, status, duration in ms, and the
    user's sub. requestId and sub come from the API Gateway event that Mangum
    keeps in scope["aws.event"]; they are null when run locally or in tests.
    JSON so Logs Insights can filter on fields. Never logs the body: here that
    would put transcripts, notes and summaries in the logs."""
    started = time.monotonic()
    response = await call_next(request)
    event = request.scope.get("aws.event") or {}
    context = event.get("requestContext") or {}
    claims = ((context.get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    log.info(json.dumps({
        "requestId": context.get("requestId"),
        "route": f"{request.method} {request.url.path}",
        "status": response.status_code,
        "ms": round((time.monotonic() - started) * 1000, 1),
        "sub": claims.get("sub"),
    }))
    return response


@app.get("/health")
def health():
    return {"ok": True, "commit": git_commit(), "function": "assistant"}


@app.post("/v1/days/{date}/finish")
def finish_day(
    body: FinishIn | None = None,
    date: str = Depends(day_date),
    user: User = Depends(current_user),
    repo: Repo = Depends(get_repo),
    summarizer=Depends(get_summarizer),
):
    """"Done for today". Writes the summary once and only once.

    The day is checked before the model runs so the common repeat costs
    nothing, and written with a condition so a true simultaneous second call
    still cannot produce a second summary."""
    body = body or FinishIn()
    day = repo.get_day(user.sub, date)
    if day is None or not day.get("exercises"):
        raise ApiError(400, "no_exercises", "Nothing is logged for that day yet.")
    if day.get("summaryGeneratedAt"):
        raise ApiError(409, "already_summarized",
                       "Today's summary is already written. You can edit it in the summary box.")

    guard_ai(repo, user)

    notes = body.notes.strip() if body.notes else None
    if notes:
        day = {**day, "notes": notes}

    history = repo.list_days(user.sub, limit=200)["days"]
    facts = summary.day_facts(day, [d for d in history if d.get("date") != date])
    text = (summarizer(summary.facts_text(facts)) or "").strip()
    if not text:
        raise ApiError(502, "ai_unavailable", "Couldn't reach the AI. Nothing was logged.")

    return {"day": repo.set_summary_once(user.sub, date, text, notes=notes)}


handler = Mangum(app, lifespan="off")
