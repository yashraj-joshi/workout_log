"""The tools the assistant can call, and the code behind them.

Two rules hold for everything here:

- The model never touches DynamoDB. Every tool goes through service.py, the
  same functions the REST routes use, so a tool call is validated exactly like
  a request from the app.
- A tool that fails returns the error to the model instead of raising, so it
  can fix the call or ask a question. Only the caller's own data is reachable:
  `sub` comes from the JWT and is never a tool argument.

The schemas are strict, which means every property must be listed in
`required` and optional ones are nullable. `_clean` drops the nulls again
before Pydantic sees them.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .. import logic, service, summary
from ..catalog import groups, muscles
from ..errors import ApiError
from ..models import DayPatch, ExerciseIn, MAX_DAY_NOTES, valid_date, valid_key
from ..repo import Repo

MAX_HISTORY_DAYS = 120
MAX_HISTORY_ROWS = 60

_SET = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reps": {"type": ["integer", "null"], "description": "Reps in this set."},
        "repsMax": {"type": ["integer", "null"], "description": "Top of a range: 10 to 12 is reps 10, repsMax 12."},
        "weight": {"type": ["number", "null"], "description": "Weight. With perHand, this is per dumbbell."},
        "seconds": {"type": ["number", "null"], "description": "Hold length, for planks and bird dogs."},
        "minutes": {"type": ["number", "null"], "description": "Cardio minutes."},
        "distance": {"type": ["number", "null"], "description": "Cardio distance."},
        "distanceUnit": {"type": ["string", "null"], "enum": ["mi", "km", None]},
        "note": {"type": ["string", "null"], "description": "Short note, e.g. 'each side'."},
    },
    "required": ["reps", "repsMax", "weight", "seconds", "minutes", "distance", "distanceUnit", "note"],
}

_SETS = {"type": "array", "items": _SET, "description": "One entry per set, in the order performed."}


def _exercise_fields(include_sets: bool = True) -> dict:
    fields = {
        "exercise": {"type": "string", "description": "Canonical name, sentence case."},
        "group": {"type": ["string", "null"], "enum": [*groups(), None]},
        "muscles": {
            "type": ["array", "null"],
            "items": {"type": "string", "enum": muscles()},
            "description": "The 2-3 worked most, main one first.",
        },
        "unit": {"type": ["string", "null"], "enum": ["lb", "kg", None]},
        "perHand": {"type": ["boolean", "null"], "description": "True only for two dumbbells."},
        "notes": {"type": ["string", "null"]},
    }
    if include_sets:
        fields["sets"] = _SETS
    return fields


def _schema(properties: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


_DATE = {"type": "string", "description": "YYYY-MM-DD."}
_KEY = {"type": "string", "description": "The two-digit key of an exercise on that day, e.g. '03'."}

SPECS: list[dict] = [
    {
        "name": "add_exercise",
        "description": "Log a new exercise on a day. The server assigns its number.",
        "parameters": _schema({"date": _DATE, **_exercise_fields()}),
    },
    {
        "name": "add_sets",
        "description": "Append sets to an exercise already logged on that day. Does not replace the existing sets.",
        "parameters": _schema({"date": _DATE, "key": _KEY, "sets": _SETS}),
    },
    {
        "name": "replace_exercise",
        "description": "Rewrite an exercise completely, for corrections. Every field is written, so send them all.",
        "parameters": _schema({"date": _DATE, "key": _KEY, **_exercise_fields()}),
    },
    {
        "name": "remove_exercise",
        "description": "Remove one exercise from a day.",
        "parameters": _schema({"date": _DATE, "key": _KEY}),
    },
    {
        "name": "update_day",
        "description": "Set the place, the day's notes or the bodyweight. Omit what you are not changing.",
        "parameters": _schema({
            "date": _DATE,
            "place": {"type": ["string", "null"], "enum": ["gym", "home", None]},
            "notes": {"type": ["string", "null"]},
            "notesMode": {
                "type": ["string", "null"],
                "enum": ["append", "replace", None],
                "description": "append adds to the existing notes; replace overwrites. Defaults to append.",
            },
            "bodyweight": {"type": ["number", "null"], "description": "In lb."},
        }),
    },
    {
        "name": "get_days",
        "description": "Read logged days in a date range, to answer a question. Oldest first.",
        "parameters": _schema({
            "from": {"type": "string", "description": "YYYY-MM-DD, inclusive."},
            "to": {"type": "string", "description": "YYYY-MM-DD, inclusive."},
        }),
    },
    {
        "name": "get_exercise_history",
        "description": "Every session of one exercise, oldest first, with its top set and the change.",
        "parameters": _schema({"name": {"type": "string"}}),
    },
    {
        "name": "finish_day",
        "description": "Write the day's summary. Runs once per day; a second call is refused.",
        "parameters": _schema({"date": _DATE, "notes": {"type": ["string", "null"]}}),
    },
]

# Which tools change data. Only these need an undo snapshot.
WRITERS = {"add_exercise", "add_sets", "replace_exercise", "remove_exercise", "update_day", "finish_day"}


def tool_params() -> list[dict]:
    """The `tools` argument for the Responses API."""
    return [{"type": "function", "name": s["name"], "description": s["description"],
             "parameters": s["parameters"], "strict": True} for s in SPECS]


def _clean(value: Any) -> Any:
    """Strict schemas make every field required, so the model sends nulls for
    the ones that do not apply. Pydantic treats absent and null differently
    (notably DayPatch, where null means remove), so they come out here."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


class Session:
    """One assistant turn's worth of work against one user's log.

    Keeps the before-snapshot of every day it touches, so the turn can be
    undone, and the after-version of each, so undo can refuse when the day has
    moved on since.
    """

    def __init__(self, repo: Repo, sub: str, today: str, finisher: Callable[[str, str | None], dict] | None = None):
        self.repo = repo
        self.sub = sub
        self.today = today
        self.finisher = finisher
        self.before: dict[str, dict | None] = {}
        self.after: dict[str, dict | None] = {}
        self.changed: list[str] = []

    def _snapshot(self, date: str) -> None:
        if date not in self.before:
            self.before[date] = self.repo.get_day(self.sub, date)

    def _record(self, date: str, day: dict | None) -> None:
        self.after[date] = day
        if date not in self.changed:
            self.changed.append(date)

    # ------------------------------------------------------------- the tools

    def run(self, name: str, raw_args: str) -> str:
        """Returns the JSON string that goes back to the model. Never raises
        for anything the model can fix: a bad call is a message, not a crash."""
        try:
            args = _clean(json.loads(raw_args or "{}"))
        except json.JSONDecodeError:
            return json.dumps({"error": "arguments were not valid JSON"})

        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return json.dumps({"error": f"no tool called {name}"})
        try:
            return json.dumps(handler(args), default=str)
        except ApiError as exc:
            # The model's own mistake: wrong key, bad muscle, day already done.
            return json.dumps({"error": exc.message, "code": exc.code})
        except (ValueError, KeyError, TypeError) as exc:
            return json.dumps({"error": str(exc)})

    def _date(self, args: dict, field: str = "date") -> str:
        try:
            return valid_date(args.get(field) or self.today)
        except ValueError as exc:
            raise ApiError(400, "validation_error", str(exc)) from exc

    def _key(self, args: dict) -> str:
        try:
            return valid_key(args.get("key") or "")
        except ValueError as exc:
            raise ApiError(400, "validation_error", str(exc)) from exc

    def _day_view(self, day: dict | None, date: str) -> dict:
        """What the model gets back after a write: enough to describe what it
        did, without the whole record."""
        if not day:
            return {"date": date, "exercises": []}
        return {
            "date": date,
            "place": logic.place_of(day)["place"],
            "placeGuessed": logic.place_of(day)["guessed"],
            "notes": day.get("notes"),
            "bodyweight": day.get("bodyweight"),
            "exercises": [
                {"key": key, "number": i + 1, "exercise": ex.get("exercise"),
                 "group": logic.group_of(ex), "sets": logic.compact_line(ex)}
                for i, (key, ex) in enumerate(sorted((day.get("exercises") or {}).items()))
            ],
        }

    def _add_exercise(self, args: dict) -> dict:
        date = self._date(args)
        self._snapshot(date)
        payload = {k: v for k, v in args.items() if k != "date"}
        day = service.add_exercise(self.repo, self.sub, date, ExerciseIn(**payload))
        self._record(date, day)
        return {"ok": True, "day": self._day_view(day, date)}

    def _add_sets(self, args: dict) -> dict:
        """Appends. The server rebuilds the whole exercise from what is stored
        plus the new sets, so the model cannot drop the existing ones."""
        date, key = self._date(args), self._key(args)
        existing = (self.repo.get_day(self.sub, date) or {}).get("exercises", {}).get(key)
        if existing is None:
            raise ApiError(404, "not_found", f"There is no exercise {key} on {date}.")
        new_sets = args.get("sets") or []
        if not new_sets:
            raise ApiError(400, "validation_error", "add_sets needs at least one set.")
        self._snapshot(date)
        merged = ExerciseIn(
            exercise=existing.get("exercise", ""),
            group=existing.get("group"),
            muscles=existing.get("muscles") or None,
            unit=existing.get("unit") or "lb",
            perHand=existing.get("perHand"),
            notes=existing.get("notes"),
            sets=[*(existing.get("sets") or []), *new_sets],
        )
        day = service.replace_exercise(self.repo, self.sub, date, key, merged)
        self._record(date, day)
        return {"ok": True, "day": self._day_view(day, date)}

    def _replace_exercise(self, args: dict) -> dict:
        date, key = self._date(args), self._key(args)
        self._snapshot(date)
        payload = {k: v for k, v in args.items() if k not in ("date", "key")}
        day = service.replace_exercise(self.repo, self.sub, date, key, ExerciseIn(**payload))
        self._record(date, day)
        return {"ok": True, "day": self._day_view(day, date)}

    def _remove_exercise(self, args: dict) -> dict:
        date, key = self._date(args), self._key(args)
        self._snapshot(date)
        day = service.remove_exercise(self.repo, self.sub, date, key)
        self._record(date, day)
        return {"ok": True, "day": self._day_view(day, date)}

    def _update_day(self, args: dict) -> dict:
        date = self._date(args)
        self._snapshot(date)
        patch: dict[str, Any] = {}
        if "place" in args:
            patch["place"] = args["place"]
        if "bodyweight" in args:
            patch["bodyweight"] = args["bodyweight"]
        if "notes" in args:
            existing = (self.before.get(date) or {}).get("notes")
            # Appending is the default: "my back felt fine" should not erase
            # what was said earlier in the session.
            if args.get("notesMode", "append") == "append" and existing:
                patch["notes"] = f"{existing} {args['notes']}".strip()[:MAX_DAY_NOTES]
            else:
                patch["notes"] = args["notes"]
        if not patch:
            raise ApiError(400, "validation_error", "update_day needs place, notes or bodyweight.")
        day = service.patch_day(self.repo, self.sub, date, DayPatch(**patch))
        self._record(date, day)
        return {"ok": True, "day": self._day_view(day, date)}

    def _get_days(self, args: dict) -> dict:
        start = self._date(args, "from")
        end = self._date(args, "to")
        if end < start:
            start, end = end, start
        days = [d for d in self.repo.list_days(self.sub, limit=500)["days"]
                if start <= (d.get("date") or "") <= end]
        days.sort(key=lambda d: d.get("date") or "")
        # Capped so one broad question cannot fill the context window.
        days = days[-MAX_HISTORY_DAYS:]
        return {
            "from": start, "to": end, "count": len(days),
            "days": [self._day_view(d, d.get("date") or "") for d in days],
        }

    def _get_exercise_history(self, args: dict) -> dict:
        name = (args.get("name") or "").strip()
        if not name:
            raise ApiError(400, "validation_error", "get_exercise_history needs a name.")
        days = self.repo.list_days(self.sub, limit=500)["days"]
        sessions = logic.exercise_sessions(days, name)[-MAX_HISTORY_ROWS:]
        rows = []
        for index, row in enumerate(sessions):
            previous = logic.previous_session(sessions, index)
            rows.append({
                "date": row["date"],
                "sets": logic.compact_line(row["exercise"]),
                "topSet": row["topSet"]["label"] if row["topSet"] else None,
                "change": logic.change_label(row["topSet"], (previous or {}).get("topSet")),
            })
        return {"exercise": name, "count": len(rows), "sessions": rows}

    def _finish_day(self, args: dict) -> dict:
        date = self._date(args)
        if self.finisher is None:
            raise ApiError(403, "not_available", "Done for today is not available here.")
        self._snapshot(date)
        day = self.finisher(date, args.get("notes"))
        self._record(date, day)
        return {"ok": True, "summary": day.get("summary"), "day": self._day_view(day, date)}

    # ------------------------------------------------------------------ undo

    def snapshots(self) -> list[dict]:
        """What the undo token needs: for each changed day, what it was before
        and the version it ended on."""
        return [{
            "date": date,
            "before": self.before.get(date),
            "afterVersion": (self.after.get(date) or {}).get("version"),
        } for date in self.changed]


def facts_for(repo: Repo, sub: str, day: dict) -> str:
    history = repo.list_days(sub, limit=200)["days"]
    facts = summary.day_facts(day, [d for d in history if d.get("date") != day.get("date")])
    return summary.facts_text(facts)
