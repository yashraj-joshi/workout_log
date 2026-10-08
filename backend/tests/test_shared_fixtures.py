"""The golden cases in shared/fixtures/ run here and in web/tests/logic.test.js.
If Python and JavaScript ever disagree about a compact line or a set tally,
one of these two suites goes red."""

from __future__ import annotations

import pytest

from workoutlog import export_csv
from workoutlog import logic as L
from conftest import load_fixture


@pytest.mark.parametrize("case", load_fixture("lookup_cases.json"),
                         ids=lambda c: c["name"])
def test_lookup(case):
    found = L.lookup(case["name"])
    assert (found["group"] if found else None) == case["group"]
    assert (found["muscles"] if found else []) == case["muscles"]


@pytest.mark.parametrize("case", load_fixture("exercise_cases.json"),
                         ids=lambda c: c["case"])
def test_exercise(case):
    exercise, want = case["exercise"], case["expect"]
    assert L.compact_line(exercise) == want["compactLine"]
    assert L.is_cardio(exercise) is want["isCardio"]
    assert L.muscles_of(exercise) == want["muscles"]
    assert L.group_of(exercise) == want["group"]
    top = L.top_set(exercise)
    if want["topSet"] is None:
        assert top is None
        return
    for field, value in want["topSet"].items():
        assert top[field] == value, field


@pytest.mark.parametrize("case", load_fixture("day_cases.json"), ids=lambda c: c["case"])
def test_day(case):
    day, want = case["day"], case["expect"]
    place = L.place_of(day)
    assert place["place"] == want["place"]
    assert place["guessed"] is want["placeGuessed"]

    totals = L.day_totals(day)
    assert totals["exercises"] == want["totals"]["exercises"]
    assert totals["sets"] == want["totals"]["sets"]
    assert totals["strengthSets"] == want["totals"]["strengthSets"]
    assert totals["reps"]["low"] == want["totals"]["repsLow"]
    assert totals["reps"]["high"] == want["totals"]["repsHigh"]
    assert totals["reps"]["hasRange"] is want["totals"]["hasRange"]
    assert totals["cardioMinutes"] == want["totals"]["cardioMinutes"]
    assert totals["cardioDistance"] == want["totals"]["cardioDistance"]

    assert L.sets_per_muscle([day]) == want["setsPerMuscle"]
    assert L.missing_areas([day]) == want["missingAreas"]
    assert L.day_load(day) == want["dayLoad"]
    assert L.heat_level(L.day_load(day)) == want["heatLevel"]


def test_formatting():
    cases = load_fixture("format_cases.json")
    for c in cases["fmtNum"]:
        assert L.fmt_num(c["in"]) == c["out"]
    for c in cases["fmtHold"]:
        assert L.fmt_hold(c["in"]) == c["out"]
    for c in cases["fmtReps"]:
        assert L.fmt_reps(c["reps"], c["repsMax"]) == c["out"]
    for c in cases["dates"]:
        assert L.fmt_date_long(c["date"], c["today"]) == c["long"]
        assert L.fmt_date_short(c["date"], c["today"]) == c["short"]
        assert L.fmt_month(c["date"]) == c["month"]
    for c in cases["changes"]:
        assert L.change_label(c["current"], c["previous"]) == c["out"]


def test_csv_matches_golden_file():
    built = export_csv.build(load_fixture("export_days.json"))
    assert built == load_fixture("export_expected.csv")


def test_csv_quotes_commas_and_skips_repeat_columns():
    rows = export_csv.rows_for(load_fixture("export_days.json"))
    header_index = {name: i for i, name in enumerate(export_csv.HEADER)}
    seated = [r for r in rows if r[header_index["Exercise"]] == "Seated row"]
    assert seated[0][header_index["Exercise note"]] == "Felt good, no back pain."
    assert seated[1][header_index["Exercise note"]] == ""
    assert seated[1][header_index["Logged at"]] == ""
    walk = [r for r in rows if r[header_index["Exercise"]] == "Treadmill walk"][0]
    assert walk[header_index["Unit"]] == ""          # no weight, so no unit
    assert walk[header_index["Distance unit"]] == "mi"
