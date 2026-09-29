"""CSV export, one row per set.

The app builds this in the browser (web/js/csv.js); this copy exists so
import_legacy.py can read back a file the old log exported, and so both
implementations can be checked against shared/fixtures/export_expected.csv.
"""

from __future__ import annotations

import csv
import io

from . import logic

HEADER = [
    "Date", "Order", "Exercise", "Area", "Muscles", "Set",
    "Reps", "Reps max", "Weight", "Unit", "Per dumbbell",
    "Hold (s)", "Time (min)", "Distance", "Distance unit",
    "Set note", "Exercise note", "Logged at",
]


def _num(value) -> str:
    """Raw values, not display-rounded: the file is for analysis, not reading."""
    if value is None:
        return ""
    number = float(value)
    return str(int(number)) if number == int(number) else str(number)


def rows_for(days: list[dict]) -> list[list[str]]:
    out: list[list[str]] = []
    for day in sorted(days, key=lambda d: d.get("date") or ""):
        date = day.get("date") or ""
        for ex in logic.exercises_in_order(day):
            unit = ex.get("unit") or "lb"
            muscles = "; ".join(logic.muscles_of(ex))
            for index, s in enumerate(ex.get("sets") or [], start=1):
                has_weight = s.get("weight") is not None
                has_distance = s.get("distance") is not None
                first = index == 1
                out.append([
                    date,
                    str(ex.get("order") or ""),
                    ex.get("exercise") or "",
                    logic.group_of(ex),
                    muscles,
                    str(index),
                    _num(s.get("reps")),
                    _num(s.get("repsMax")),
                    _num(s.get("weight")),
                    unit if has_weight else "",
                    "yes" if ex.get("perHand") else "",
                    _num(s.get("seconds")),
                    _num(s.get("minutes")),
                    _num(s.get("distance")),
                    (s.get("distanceUnit") or "mi") if has_distance else "",
                    s.get("note") or "",
                    # Only on the exercise's first row, so a spreadsheet does
                    # not repeat the same note down every set.
                    (ex.get("notes") or "") if first else "",
                    (ex.get("loggedAt") or "") if first else "",
                ])
    return out


def build(days: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(rows_for(days))
    return buffer.getvalue()


def filename(today: str) -> str:
    return f"workout-log-{today}.csv"
