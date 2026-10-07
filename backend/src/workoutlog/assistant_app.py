"""AssistantFunction: the routes that spend OpenAI credit.

Split from ApiFunction for one reason: this is the only function with
ssm:GetParameter on the OpenAI key, and the only one that imports the SDK.

Three routes:
- POST /v1/assistant          voice or typed, logs through the same service
                              functions the REST routes use
- POST /v1/assistant/undo     puts back the snapshot that turn replaced
- POST /v1/days/{date}/finish "Done for today", once per day
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Path, Request
from mangum import Mangum

from . import errors, service
from .assistant import tools
from .auth import User, current_user, git_commit, require_ai
from .errors import ApiError
from .idempotency import Claim, idempotency_key, replay, request_hash
from .models import AssistantIn, FinishIn, UndoIn, valid_date
from .repo import Repo, public_day

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


def _write_summary(facts_text: str) -> str:
    # Imported here, not at module level: the SDK costs cold-start time, and
    # most finish requests that stop early (400, 409, 403, 429) never need it.
    from .assistant.summarizer import write_summary
    return write_summary(facts_text)


def get_summarizer():
    """Injectable so tests never reach OpenAI. Returns a function that imports
    the SDK only when it is called, after every cheaper check has passed."""
    return _write_summary


def _transcribe(audio: bytes, mime: str, names: list[str]):
    from .assistant.transcribe import transcribe
    return transcribe(audio, mime, names)


def get_transcriber():
    """Injectable for the same reason as the summarizer."""
    return _transcribe


def _run_agent(**kwargs):
    from .assistant.agent import run
    return run(**kwargs)


def get_agent():
    return _run_agent


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


# --------------------------------------------------------------------------
# The assistant
# --------------------------------------------------------------------------

def _known_names(days: list[dict], limit: int = 60) -> list[str]:
    """Exercise names this user already uses, most recently used first. They
    prime the transcriber and tell the model which spelling to reuse."""
    seen: dict[str, str] = {}
    for day in sorted(days, key=lambda d: d.get("date") or "", reverse=True):
        for ex in sorted((day.get("exercises") or {}).values(), key=lambda e: e.get("order") or 0):
            name = (ex.get("exercise") or "").strip()
            key = name.lower()
            if name and key not in seen:
                seen[key] = name
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]


@app.post("/v1/assistant")
def assistant(
    body: AssistantIn,
    request: Request,
    user: User = Depends(current_user),
    repo: Repo = Depends(get_repo),
    transcriber=Depends(get_transcriber),
    agent=Depends(get_agent),
    summarizer=Depends(get_summarizer),
    key: str | None = Depends(idempotency_key),
):
    """One turn: hear it, decide what it means, write it, say what was written.

    Everything the model does goes through service.py, so a tool call is
    validated exactly like a request from the app, and nothing it can do
    reaches outside this user's own partition.
    """
    guard_ai(repo, user)

    # This route makes several writes and two paid calls, so it cannot put the
    # key in one transaction the way the REST writes do. It claims the key
    # first and marks it done at the end; a retry of a turn that already ran
    # replays the answer instead of logging it twice.
    expected = request_hash(request, body) if key else None
    claim = Claim(repo, user.sub, key, expected)
    if claim.taken:
        repo.release_usage(user.sub, utc_today())
        return replay(claim.taken, expected)

    history = repo.list_days(user.sub, limit=500)["days"]
    names = _known_names(history)
    audio_seconds = 0.0
    # Declared before the try so the failure path can ask whether anything was
    # written, even when the failure came before the session existed.
    session = tools.Session(repo, user.sub, body.today,
                            finisher=lambda date, notes: _finish(repo, user, date, notes, summarizer))

    try:
        if body.audioBase64:
            try:
                raw = body.audio()
            except ValueError as exc:
                raise ApiError(413, "audio_too_large", str(exc)) from exc
            transcript, audio_seconds = transcriber(raw, body.audioMimeType, names)
            # Stripped here rather than trusting the transcriber: silence can
            # come back as a space, and an empty turn is not worth a model call.
            transcript = (transcript or "").strip()
            if not transcript:
                raise ApiError(400, "no_speech", "I couldn't hear anything in that. Try again.")
        else:
            transcript = body.text

        day = repo.get_day(user.sub, body.date)
        turns = repo.recent_turns(user.sub, body.date, limit=3)
        # finish_day goes through the same run-once path as the button, which
        # is why the summarizer is handed down rather than reimplemented.
        context = _context(today=body.today, timezone=body.timezone, date=body.date,
                           day=day, known_names=names, turns=turns)
        result = agent(session=session, context=context, said=transcript)
    except ApiError as exc:
        repo.release_usage(user.sub, utc_today())
        # Nothing was written: give the key back so a retry runs fresh.
        if not session.changed:
            claim.release()
            raise
        # Some of it was logged before this went wrong. Saying "nothing was
        # logged" would be untrue, and leaving the key claimed would make the
        # app retry something that already half-happened. So the turn ends
        # here, with what was done and a way to undo it.
        result = {"reply": exc.message, "assumptions": [], "question": None,
                  "modelCalls": 0, "inputTokens": 0, "outputTokens": 0}

    snapshots = session.snapshots()
    token = repo.put_turn(user.sub, {
        "date": body.date,
        "transcript": transcript,
        "reply": result["reply"],
        "question": result.get("question"),
        "changedDates": session.changed,
        "snapshots": snapshots,
    }) if snapshots or result["reply"] else None

    _record_cost("assistant", result, audio_seconds)

    answer = {
        "transcript": transcript,
        "reply": result["reply"],
        "assumptions": result.get("assumptions") or [],
        "question": result.get("question"),
        "changedDates": session.changed,
        "days": [public_day(repo.get_day(user.sub, d) or {"date": d}) if repo.get_day(user.sub, d)
                 else {"date": d, "deleted": True} for d in session.changed],
        "undoToken": token if snapshots else None,
    }
    claim.finish(answer)
    return answer


def _context(**kwargs) -> str:
    from .assistant.agent import build_context
    return build_context(**kwargs)


def _record_cost(route: str, result: dict, audio_seconds: float) -> None:
    from .assistant.metrics import record
    record(route,
           input_tokens=result.get("inputTokens", 0),
           output_tokens=result.get("outputTokens", 0),
           audio_seconds=audio_seconds,
           model_calls=result.get("modelCalls", 0))


@app.post("/v1/assistant/undo")
def undo(body: UndoIn, user: User = Depends(current_user), repo: Repo = Depends(get_repo)):
    """Puts each changed day back as it was, but only if nothing has touched it
    since. Anything else would quietly throw away a later edit."""
    require_ai(user)
    turn = repo.get_turn(user.sub, body.undoToken)
    if turn is None:
        raise ApiError(404, "not_found", "That change is too old to undo.")
    if turn.get("undone"):
        raise ApiError(409, "already_undone", "That change was already undone.")

    snapshots = turn.get("snapshots") or []
    if not snapshots:
        raise ApiError(400, "nothing_to_undo", "That turn didn't change anything.")

    for snap in snapshots:
        current = repo.get_day(user.sub, snap["date"])
        if (current or {}).get("version") != snap.get("afterVersion"):
            raise ApiError(409, "day_changed",
                           f"{snap['date']} has changed since then, so it wasn't undone.")

    days = []
    for snap in snapshots:
        before = snap.get("before")
        current = repo.get_day(user.sub, snap["date"])
        # Undo never un-writes the summary: "Done for today" is once per day,
        # and taking the marker away would hand back a second free generation.
        if before is None and current and current.get("summaryGeneratedAt"):
            before = {k: v for k, v in current.items() if k in ("date", "summary", "summaryGeneratedAt")}
        elif before is not None and current and current.get("summaryGeneratedAt") and not before.get("summaryGeneratedAt"):
            before = {**before,
                      "summary": current.get("summary"),
                      "summaryGeneratedAt": current["summaryGeneratedAt"]}
        restored = service.replace_day(repo, user.sub, snap["date"], before)
        days.append(public_day(restored) if restored else {"date": snap["date"], "deleted": True})

    repo.mark_turn_undone(user.sub, body.undoToken)
    return {"undone": True, "changedDates": [s["date"] for s in snapshots], "days": days}


def _finish(repo: Repo, user: User, date: str, notes: str | None, summarizer) -> dict:
    """The body of "Done for today", shared by the route and the tool."""
    day = repo.get_day(user.sub, date)
    if day is None or not day.get("exercises"):
        raise ApiError(400, "no_exercises", "Nothing is logged for that day yet.")
    if day.get("summaryGeneratedAt"):
        raise ApiError(409, "already_summarized",
                       "Today's summary is already written. You can edit it in the summary box.")
    notes = notes.strip() if notes else None
    if notes:
        day = {**day, "notes": notes}
    text = (summarizer(tools.facts_for(repo, user.sub, day)) or "").strip()
    if not text:
        raise ApiError(502, "ai_unavailable", "Couldn't reach the AI. Nothing was logged.")
    return repo.set_summary_once(user.sub, date, text, notes=notes)


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
    # Checked before the counter moves, so the common repeat costs nothing.
    day = repo.get_day(user.sub, date)
    if day is None or not day.get("exercises"):
        raise ApiError(400, "no_exercises", "Nothing is logged for that day yet.")
    if day.get("summaryGeneratedAt"):
        raise ApiError(409, "already_summarized",
                       "Today's summary is already written. You can edit it in the summary box.")

    guard_ai(repo, user)
    try:
        return {"day": _finish(repo, user, date, body.notes, summarizer)}
    except ApiError:
        # No summary was written, so the attempt shouldn't use up one of the
        # day's calls. None of these failures is something a user can trigger.
        repo.release_usage(user.sub, utc_today())
        raise


handler = Mangum(app, lifespan="off")
