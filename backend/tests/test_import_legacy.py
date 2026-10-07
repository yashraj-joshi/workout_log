"""scripts/import_legacy.py: reading both old formats and validating them.

The AWS half (finding the user, writing the days) is covered by the dry run
in docs/07; what is tested here is the part that can get the data wrong.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from workoutlog import export_csv

ROOT = Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location("import_legacy", ROOT / "scripts" / "import_legacy.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------- JSON

def test_a_day_from_the_old_json_comes_through_whole():
    day = legacy.build_day({
        "date": "2026-09-21",
        "place": "gym",
        "summary": "Back and legs.",
        "notes": "Back felt fine.",
        "bodyweight": 180.4,
        "exercises": {
            "01": {"exercise": "Seated row", "group": "Back", "muscles": ["Mid back", "Lats"],
                   "unit": "lb", "loggedAt": "2026-09-21T14:33:00Z",
                   "sets": [{"reps": 10, "repsMax": 12, "weight": 40}] * 3},
        },
    })

    assert day["place"] == "gym"
    assert day["notes"] == "Back felt fine."
    assert day["bodyweight"] == 180.4
    assert day["exercises"]["01"]["order"] == 1
    assert day["exercises"]["01"]["loggedAt"] == "2026-09-21T14:33:00Z"
    assert len(day["exercises"]["01"]["sets"]) == 3


def test_an_imported_summary_counts_as_already_written():
    day = legacy.build_day({"date": "2026-09-21", "summary": "Back and legs.",
                            "exercises": {}})
    assert day["summaryGeneratedAt"], "otherwise the app would offer to write a second one"


def test_a_day_with_no_summary_has_no_marker():
    day = legacy.build_day({"date": "2026-09-21", "exercises": {
        "01": {"exercise": "Plank", "sets": [{"seconds": 45}]}}})
    assert "summaryGeneratedAt" not in day


def test_exercises_are_renumbered_in_order_with_no_gaps():
    day = legacy.build_day({"date": "2026-09-21", "exercises": {
        "03": {"exercise": "Plank", "sets": [{"seconds": 45}]},
        "07": {"exercise": "Seated row", "sets": [{"reps": 10, "weight": 40}]},
    }})
    assert list(day["exercises"]) == ["01", "02"]
    assert day["exercises"]["01"]["exercise"] == "Plank", "the old order survives"
    assert day["exercises"]["02"]["order"] == 2


def test_a_list_of_exercises_works_as_well_as_a_map():
    day = legacy.build_day({"date": "2026-09-21", "exercises": [
        {"exercise": "Plank", "sets": [{"seconds": 45}]},
    ]})
    assert day["exercises"]["01"]["exercise"] == "Plank"


def test_the_area_and_muscles_are_filled_in_when_the_old_log_had_none():
    day = legacy.build_day({"date": "2026-09-21", "exercises": {
        "01": {"exercise": "Lat pulldown", "sets": [{"reps": 10, "weight": 70}]}}})
    assert day["exercises"]["01"]["group"] == "Back"
    assert day["exercises"]["01"]["muscles"][0] == "Lats"


def test_a_bad_record_is_named_rather_than_imported():
    with pytest.raises(legacy.Problem) as caught:
        legacy.build_day({"date": "2026-09-21", "exercises": {
            "01": {"exercise": "Seated row", "muscles": ["Pecs"], "sets": [{"reps": 10}]}}})
    assert "Seated row" in str(caught.value)
    assert "Pecs" in str(caught.value)


def test_a_date_that_is_not_a_date_is_refused():
    for date in ["", "21/09/2026", "2026-13-01"]:
        with pytest.raises(ValueError):
            legacy.build_day({"date": date, "exercises": {}})


def test_a_day_with_nothing_in_it_is_refused():
    with pytest.raises(legacy.Problem, match="nothing worth importing"):
        legacy.build_day({"date": "2026-09-21", "exercises": {}})


def test_an_exercise_with_no_sets_is_dropped_not_fatal():
    day = legacy.build_day({"date": "2026-09-21", "exercises": {
        "01": {"exercise": "Ghost", "sets": []},
        "02": {"exercise": "Plank", "sets": [{"seconds": 45}]},
    }})
    assert [ex["exercise"] for ex in day["exercises"].values()] == ["Plank"]


# -------------------------------------------------------------------- CSV

def test_the_old_csv_round_trips_through_the_importer():
    """The strongest check there is: export the fixture days with the real
    exporter, read the file back, and the days should match."""
    days = json.loads((ROOT / "shared" / "fixtures" / "export_days.json").read_text())
    text = export_csv.build(days)

    rebuilt = {d["date"]: legacy.build_day(d) for d in legacy.read_csv(text)}

    for original in days:
        got = rebuilt[original["date"]]
        for key, ex in sorted((original.get("exercises") or {}).items()):
            mine = got["exercises"][key]
            assert mine["exercise"] == ex["exercise"]
            assert mine["sets"] == ex["sets"], f"{original['date']} {key}"
            assert mine["unit"] == ex.get("unit", "lb")
            assert mine.get("notes") == ex.get("notes")
            assert mine["muscles"] == ex["muscles"]


def test_the_csv_keeps_per_dumbbell_and_the_logged_time():
    days = [{"date": "2026-09-21", "exercises": {"01": {
        "order": 1, "exercise": "Dumbbell bench press", "group": "Chest",
        "muscles": ["Chest", "Triceps"], "unit": "lb", "perHand": True,
        "loggedAt": "2026-09-21T14:33:00Z",
        "sets": [{"reps": 10, "weight": 25}, {"reps": 8, "weight": 30}]}}}]
    [rebuilt] = legacy.read_csv(export_csv.build(days))
    day = legacy.build_day(rebuilt)

    assert day["exercises"]["01"]["perHand"] is True
    assert day["exercises"]["01"]["loggedAt"] == "2026-09-21T14:33:00Z"


def test_the_csv_carries_no_place_summary_notes_or_bodyweight():
    """The old export never had them, so an import from CSV must not invent
    them - that is what the JSON export is for."""
    days = [{"date": "2026-09-21", "place": "gym", "summary": "Back day.",
             "notes": "fine", "bodyweight": 180,
             "exercises": {"01": {"order": 1, "exercise": "Plank", "group": "Core",
                                  "muscles": ["Core"], "unit": "lb",
                                  "sets": [{"seconds": 45}]}}}]
    [rebuilt] = legacy.read_csv(export_csv.build(days))
    day = legacy.build_day(rebuilt)

    for field in ("place", "summary", "notes", "bodyweight", "summaryGeneratedAt"):
        assert field not in day, field


def test_the_same_exercise_twice_in_a_day_stays_two_exercises():
    text = "\n".join([
        ",".join(export_csv.HEADER),
        "2026-09-21,1,Seated row,Back,Mid back,1,10,,40,lb,,,,,,,,",
        "2026-09-21,2,Seated row,Back,Mid back,1,8,,45,lb,,,,,,,,",
    ]) + "\n"
    [day] = legacy.read_csv(text)
    built = legacy.build_day(day)
    assert len(built["exercises"]) == 2
    assert built["exercises"]["02"]["sets"] == [{"reps": 8, "weight": 45}]


def test_a_csv_that_is_not_the_export_says_so():
    with pytest.raises(legacy.Problem, match="old Export CSV"):
        legacy.read_csv("Name,Score\nYash,10\n")


def test_a_number_column_with_words_in_it_names_the_row():
    text = "\n".join([
        ",".join(export_csv.HEADER),
        "2026-09-21,1,Seated row,Back,Mid back,1,ten,,40,lb,,,,,,,,",
    ]) + "\n"
    with pytest.raises(legacy.Problem, match="row 2"):
        legacy.read_csv(text)


# ------------------------------------------------------------------ files

def test_the_format_is_chosen_by_what_the_file_holds(tmp_path):
    days, fmt = legacy.load(write(tmp_path, "log.json", json.dumps([{"date": "2026-09-21"}])))
    assert fmt == "JSON" and len(days) == 1

    # A .txt file holding JSON is still JSON.
    _, fmt = legacy.load(write(tmp_path, "log.txt", json.dumps([{"date": "2026-09-21"}])))
    assert fmt == "JSON"

    text = "\n".join([",".join(export_csv.HEADER),
                      "2026-09-21,1,Plank,Core,Core,1,,,,,,45,,,,,,"]) + "\n"
    _, fmt = legacy.load(write(tmp_path, "log.csv", text))
    assert fmt == "CSV"


def test_an_export_wrapped_in_an_object_is_unwrapped():
    assert legacy.read_json(json.dumps({"days": [{"date": "2026-09-21"}]})) == [{"date": "2026-09-21"}]


def test_a_missing_file_and_broken_json_say_which(tmp_path):
    with pytest.raises(legacy.Problem, match="no such file"):
        legacy.load(tmp_path / "nope.json")
    with pytest.raises(legacy.Problem, match="isn't valid JSON"):
        legacy.load(write(tmp_path, "bad.json", "{not json"))


def test_a_byte_order_mark_from_excel_does_not_break_the_first_column(tmp_path):
    text = "﻿" + ",".join(export_csv.HEADER) + "\n2026-09-21,1,Plank,Core,Core,1,,,,,,45,,,,,,\n"
    days, fmt = legacy.load(write(tmp_path, "excel.csv", text))
    assert fmt == "CSV"
    assert days[0]["date"] == "2026-09-21"
