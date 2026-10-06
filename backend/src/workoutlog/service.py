"""Day operations, written once so the REST routes and the assistant's tools
cannot drift apart. Everything here takes a Repo and the caller's sub; nothing
here reads the request."""

from __future__ import annotations

from typing import Any

from .errors import ApiError
from .models import MAX_EXERCISES_PER_DAY, DayPatch, ExerciseIn
from .repo import Repo, RequestRecord, next_order, now_iso, prune_day as _prune


def _blank(date: str) -> dict:
    return {"date": date, "exercises": {}}


def get_day(repo: Repo, sub: str, date: str) -> dict:
    day = repo.get_day(sub, date)
    if day is None:
        raise ApiError(404, "not_found", f"Nothing logged for {date}.")
    return day


def patch_day(repo: Repo, sub: str, date: str, patch: DayPatch) -> dict | None:
    """Creates the day if it does not exist. A field sent as null is removed.
    Editing the summary never clears summaryGeneratedAt: the run-once rule is
    about generating, not about editing."""
    changes = patch.changes()

    def apply(day: dict | None) -> dict | None:
        day = day or _blank(date)
        for field, value in changes.items():
            if value is None:
                day.pop(field, None)
            else:
                day[field] = value
        return _prune(day)

    return repo.mutate_day(sub, date, apply)


def add_exercise(repo: Repo, sub: str, date: str, exercise: ExerciseIn,
                 logged_at: str | None = None, request: RequestRecord | None = None) -> dict:
    stamp = logged_at or now_iso()

    def apply(day: dict | None) -> dict:
        day = day or _blank(date)
        exercises = day.setdefault("exercises", {})
        if len(exercises) >= MAX_EXERCISES_PER_DAY:
            raise ApiError(400, "day_full", "That day already has 99 exercises.")
        order = next_order(exercises)
        exercises[f"{order:02d}"] = exercise.stored(order, stamp)
        return day

    return repo.mutate_day(sub, date, apply, request=request)


def replace_exercise(repo: Repo, sub: str, date: str, key: str, exercise: ExerciseIn) -> dict:
    """A full replace, so an edit never leaves a stale field behind. Order and
    loggedAt are the server's and survive the edit."""

    def apply(day: dict | None) -> dict:
        existing = ((day or {}).get("exercises") or {}).get(key)
        if existing is None:
            raise ApiError(404, "not_found", "That exercise is not on that day.")
        day["exercises"][key] = exercise.stored(
            order=int(existing.get("order") or int(key)),
            logged_at=existing.get("loggedAt") or now_iso(),
        )
        return day

    return repo.mutate_day(sub, date, apply)


def remove_exercise(repo: Repo, sub: str, date: str, key: str) -> dict | None:
    seen: dict[str, Any] = {}

    def apply(day: dict | None) -> dict | None:
        if not day or key not in (day.get("exercises") or {}):
            raise ApiError(404, "not_found", "That exercise is not on that day.")
        seen["removed"] = day["exercises"].pop(key)
        return _prune(day)

    return repo.mutate_day(sub, date, apply)


def move_exercise(repo: Repo, sub: str, date: str, key: str, to_date: str,
                  request: RequestRecord | None = None) -> dict:
    return repo.move_exercise(sub, date, to_date, key, request=request)


def replace_day(repo: Repo, sub: str, date: str, day: dict | None) -> dict | None:
    """Used by undo, which restores a whole snapshot."""

    def apply(_current: dict | None) -> dict | None:
        return _prune(dict(day) if day else None)

    return repo.mutate_day(sub, date, apply)
