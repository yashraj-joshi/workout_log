"""Workout logic shared with the frontend.

Every function here has a line-for-line twin in web/js/logic.js, and
shared/fixtures/ is run against both so the two can't drift. Change one,
change the other, and add a fixture case.

These functions take plain dicts (not Pydantic models) so that the Python and
JavaScript versions read the same and can share the same fixture files.
"""

from __future__ import annotations

import re
from datetime import date as _date

from . import catalog

EN_DASH = "–"
TIMES = "×"
MIDDOT = " · "

_MONTHS_LONG = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


# --------------------------------------------------------------------------
# Name lookup
# --------------------------------------------------------------------------

_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_words(text: str) -> list[str]:
    """Lowercase word tokens. Hyphens split, so "push-up" == "push up"."""
    return [w for w in _NON_WORD.sub(" ", (text or "").lower()).split(" ") if w]


def _contains_phrase(words: list[str], phrase: list[str]) -> bool:
    """True when `phrase` appears as a run of whole words inside `words`."""
    if not phrase or len(phrase) > len(words):
        return False
    for i in range(len(words) - len(phrase) + 1):
        if words[i:i + len(phrase)] == phrase:
            return True
    return False


def lookup(name: str) -> dict | None:
    """First matching rule wins, so the catalog's rule order is significant."""
    words = normalize_words(name)
    if not words:
        return None
    for rule in catalog.lookup_rules():
        for word in rule["words"]:
            if _contains_phrase(words, normalize_words(word)):
                return {"group": rule["group"], "muscles": list(rule["muscles"])}
    return None


def group_of(exercise: dict) -> str:
    group = exercise.get("group")
    if group:
        return group
    found = lookup(exercise.get("exercise", ""))
    return found["group"] if found else "Mobility"


def muscles_of(exercise: dict) -> list[str]:
    """The exercise's own muscles (up to 4); otherwise look them up by name."""
    own = exercise.get("muscles") or []
    if own:
        return list(own[:4])
    found = lookup(exercise.get("exercise", ""))
    return list(found["muscles"]) if found else []


# --------------------------------------------------------------------------
# Set and exercise shape
# --------------------------------------------------------------------------

def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def is_cardio(exercise: dict) -> bool:
    """Group says Cardio, or every set is time/distance only."""
    if group_of(exercise) == "Cardio":
        return True
    sets = exercise.get("sets") or []
    if not sets:
        return False
    for s in sets:
        timed = _num(s.get("minutes")) is not None or _num(s.get("distance")) is not None
        other = any(_num(s.get(f)) is not None for f in ("reps", "weight", "seconds"))
        if not timed or other:
            return False
    return True


def set_identity(s: dict) -> tuple:
    """Fields that must match for two sets to merge in the compact line."""
    return (
        s.get("reps"), s.get("repsMax"), s.get("weight"),
        s.get("seconds"), s.get("minutes"),
        s.get("distance"), s.get("distanceUnit") or "mi",
        s.get("note") or "",
    )


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def fmt_num(value) -> str:
    """One decimal at most, with thousands separators. 1.07 -> "1.1"."""
    if value is None:
        return "—"
    rounded = round(float(value) + 0.0, 1)
    if rounded == int(rounded):
        return f"{int(rounded):,d}"
    return f"{rounded:,.1f}"


def fmt_reps(reps, reps_max=None) -> str:
    if reps is None:
        return ""
    if reps_max is not None and reps_max != reps:
        return f"{fmt_num(reps)}{EN_DASH}{fmt_num(reps_max)}"
    return fmt_num(reps)


def fmt_hold(seconds) -> str:
    """Under two minutes reads as seconds; longer reads as m:ss."""
    if seconds is None:
        return ""
    total = float(seconds)
    if total < 120:
        return f"{fmt_num(total)} s"
    whole = int(round(total))
    return f"{whole // 60}:{whole % 60:02d} min"


def fmt_weight(weight, unit="lb") -> str:
    return f"{fmt_num(weight)} {unit or 'lb'}"


def fmt_distance(distance, unit="mi") -> str:
    return f"{fmt_num(distance)} {unit or 'mi'}"


def set_label(s: dict, unit: str = "lb") -> str:
    parts = []
    if _num(s.get("reps")) is not None:
        parts.append(fmt_reps(s.get("reps"), s.get("repsMax")))
    if _num(s.get("seconds")) is not None:
        parts.append(fmt_hold(s.get("seconds")))
    if _num(s.get("minutes")) is not None:
        parts.append(f"{fmt_num(s['minutes'])} min")
    if _num(s.get("distance")) is not None:
        parts.append(fmt_distance(s.get("distance"), s.get("distanceUnit")))
    base = ", ".join(parts)
    if _num(s.get("weight")) is not None:
        weight = fmt_weight(s["weight"], unit)
        return f"{base} @ {weight}" if base else weight
    return base


def compact_line(exercise: dict) -> str:
    """Consecutive identical sets merge: "3 x 10-12 @ 40 lb - 8 @ 30 lb"."""
    sets = exercise.get("sets") or []
    unit = exercise.get("unit") or "lb"
    chunks: list[str] = []
    index = 0
    while index < len(sets):
        run = 1
        while index + run < len(sets) and set_identity(sets[index + run]) == set_identity(sets[index]):
            run += 1
        label = set_label(sets[index], unit)
        if label:
            chunks.append(f"{run} {TIMES} {label}" if run > 1 else label)
        index += run
    return MIDDOT.join(chunks)


# --------------------------------------------------------------------------
# Top set
# --------------------------------------------------------------------------

def top_set(exercise: dict) -> dict | None:
    """Heaviest set wins; ties go to more reps. With no weight anywhere, fall
    back to the longest hold, then the most minutes, then the most reps."""
    sets = exercise.get("sets") or []
    unit = exercise.get("unit") or "lb"

    weighted = [s for s in sets if _num(s.get("weight")) is not None]
    if weighted:
        best = max(weighted, key=lambda s: (float(s["weight"]), float(s.get("reps") or 0)))
        reps = fmt_reps(best.get("reps"), best.get("repsMax"))
        label = fmt_weight(best["weight"], unit)
        if reps:
            label = f"{label} {TIMES} {reps}"
        return {"kind": "weight", "value": float(best["weight"]), "unit": unit,
                "reps": best.get("reps"), "repsMax": best.get("repsMax"), "label": label}

    held = [s for s in sets if _num(s.get("seconds")) is not None]
    if held:
        best = max(held, key=lambda s: float(s["seconds"]))
        return {"kind": "seconds", "value": float(best["seconds"]), "unit": "sec",
                "reps": None, "repsMax": None, "label": fmt_hold(best["seconds"])}

    timed = [s for s in sets if _num(s.get("minutes")) is not None]
    if timed:
        best = max(timed, key=lambda s: float(s["minutes"]))
        return {"kind": "minutes", "value": float(best["minutes"]), "unit": "min",
                "reps": None, "repsMax": None, "label": f"{fmt_num(best['minutes'])} min"}

    repped = [s for s in sets if _num(s.get("reps")) is not None]
    if repped:
        best = max(repped, key=lambda s: float(s["reps"]))
        return {"kind": "reps", "value": float(best["reps"]), "unit": "reps",
                "reps": best.get("reps"), "repsMax": best.get("repsMax"),
                "label": f"{fmt_reps(best.get('reps'), best.get('repsMax'))} reps"}

    return None


# --------------------------------------------------------------------------
# Day-level rollups
# --------------------------------------------------------------------------

def exercises_in_order(day: dict) -> list[dict]:
    exercises = (day or {}).get("exercises") or {}
    return [exercises[k] for k in sorted(exercises.keys())]


def place_of(day: dict) -> dict:
    """An explicit place wins. Otherwise weighted strength work means gym."""
    place = (day or {}).get("place")
    if place in ("gym", "home"):
        return {"place": place, "guessed": False}
    for ex in exercises_in_order(day):
        if is_cardio(ex):
            continue
        if any(_num(s.get("weight")) is not None for s in ex.get("sets") or []):
            return {"place": "gym", "guessed": True}
    return {"place": "home", "guessed": True}


def reps_total(day: dict) -> dict:
    low = 0.0
    high = 0.0
    has_range = False
    for ex in exercises_in_order(day):
        for s in ex.get("sets") or []:
            reps = _num(s.get("reps"))
            if reps is None:
                continue
            reps_max = _num(s.get("repsMax"))
            low += reps
            high += reps_max if reps_max is not None else reps
            if reps_max is not None and reps_max != reps:
                has_range = True
    return {"low": low, "high": high, "hasRange": has_range}


def day_totals(day: dict) -> dict:
    exercises = exercises_in_order(day)
    sets = sum(len(ex.get("sets") or []) for ex in exercises)
    reps = reps_total(day)
    cardio = cardio_totals([day])
    strength_sets = sum(len(ex.get("sets") or []) for ex in exercises if not is_cardio(ex))
    return {
        "exercises": len(exercises),
        "sets": sets,
        "strengthSets": strength_sets,
        "reps": reps,
        "cardioMinutes": cardio["minutes"],
        "cardioDistance": cardio["distance"],
    }


def sets_per_muscle(days: list[dict]) -> list[dict]:
    """Every set counts toward every muscle the exercise lists. Cardio
    exercises are left out; their minutes are reported separately."""
    tally: dict[str, int] = {}
    for day in days:
        for ex in exercises_in_order(day):
            if is_cardio(ex):
                continue
            count = len(ex.get("sets") or [])
            if not count:
                continue
            for muscle in muscles_of(ex):
                if muscle == "Cardio":
                    continue
                tally[muscle] = tally.get(muscle, 0) + count
    rows = [{"muscle": m, "sets": c} for m, c in tally.items()]
    rows.sort(key=lambda r: (-r["sets"], r["muscle"]))
    return rows


def cardio_totals(days: list[dict]) -> dict:
    minutes = 0.0
    distance = 0.0
    for day in days:
        for ex in exercises_in_order(day):
            if not is_cardio(ex):
                continue
            for s in ex.get("sets") or []:
                minutes += _num(s.get("minutes")) or 0
                distance += _num(s.get("distance")) or 0
    return {"minutes": round(minutes, 4), "distance": round(distance, 4)}


def day_load(day: dict) -> int:
    """Heatmap weight for a day: a cardio exercise counts as one set."""
    total = 0
    for ex in exercises_in_order(day):
        total += 1 if is_cardio(ex) else len(ex.get("sets") or [])
    return total


def heat_level(load: int) -> int:
    if load <= 0:
        return 0
    if load <= 5:
        return 1
    if load <= 11:
        return 2
    return 3


def missing_areas(days: list[dict]) -> list[str]:
    worked = {row["muscle"] for row in sets_per_muscle(days)}
    return [a["label"] for a in catalog.main_areas() if not worked.intersection(a["muscles"])]


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def parse_date(value: str) -> _date:
    year, month, day = (int(p) for p in value.split("-"))
    return _date(year, month, day)


def fmt_date_long(value: str, today: str | None = None) -> str:
    d = parse_date(value)
    text = f"{_WEEKDAYS[(d.weekday() + 1) % 7]}, {_MONTHS_LONG[d.month - 1]} {d.day}"
    if today and parse_date(today).year != d.year:
        text += f", {d.year}"
    return text


def fmt_month(value: str) -> str:
    """The calendar's month heading: "September 2026"."""
    d = parse_date(value)
    return f"{_MONTHS_LONG[d.month - 1]} {d.year}"


def fmt_date_short(value: str, today: str | None = None) -> str:
    d = parse_date(value)
    text = f"{_MONTHS_SHORT[d.month - 1]} {d.day}"
    if today and parse_date(today).year != d.year:
        text += f", {d.year}"
    return text


# --------------------------------------------------------------------------
# Sessions of one exercise across days (Progress tab, and the day summary)
# --------------------------------------------------------------------------

MINUS = "−"


def same_exercise(a: str, b: str) -> bool:
    """Names match on words, so "Seated Row" and "seated row" are one exercise."""
    return normalize_words(a) == normalize_words(b)


def exercise_sessions(days: list[dict], name: str) -> list[dict]:
    """Oldest first, one entry per day that contains the exercise."""
    out = []
    for day in days:
        for ex in exercises_in_order(day):
            if same_exercise(ex.get("exercise", ""), name):
                out.append({"date": day.get("date"), "exercise": ex, "topSet": top_set(ex)})
    out.sort(key=lambda row: row["date"] or "")
    return out


def dominant_kind(sessions: list[dict]) -> str | None:
    """The chart plots the kind most sessions have, so one odd session does not
    change the axis."""
    counts: dict[str, int] = {}
    for row in sessions:
        top = row.get("topSet")
        if top:
            counts[top["kind"]] = counts.get(top["kind"], 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def previous_session(sessions: list[dict], index: int) -> dict | None:
    """The newest earlier session whose top set is the same kind."""
    current = sessions[index].get("topSet")
    if not current:
        return None
    for earlier in range(index - 1, -1, -1):
        top = sessions[earlier].get("topSet")
        if top and top["kind"] == current["kind"]:
            return sessions[earlier]
    return None


def change_label(current: dict | None, previous: dict | None) -> str:
    """"+5 lb", "-2 reps", "same" or "first"."""
    if not current:
        return ""
    if not previous or previous["kind"] != current["kind"]:
        return "first"

    def signed(delta: float, unit: str) -> str:
        sign = "+" if delta > 0 else MINUS
        return f"{sign}{fmt_num(abs(delta))} {unit}"

    unit = {"weight": current.get("unit") or "lb", "seconds": "sec",
            "minutes": "min", "reps": "reps"}[current["kind"]]
    delta = current["value"] - previous["value"]
    if delta:
        return signed(delta, unit)
    if current["kind"] == "weight":
        rep_delta = float(current.get("reps") or 0) - float(previous.get("reps") or 0)
        if rep_delta:
            return signed(rep_delta, "reps")
    return "same"
