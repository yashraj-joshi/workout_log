// The golden cases in shared/fixtures/ run here and in
// backend/tests/test_shared_fixtures.py. If JavaScript and Python ever
// disagree about a compact line or a set tally, one of the two suites goes red.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { setCatalog } from "../js/catalog.js";
import * as L from "../js/logic.js";

const read = (path) => JSON.parse(readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8"));
const fixture = (name) => read(`../../shared/fixtures/${name}`);

// The copy the app actually fetches. test_catalog_sync.py proves it matches
// shared/exercise_catalog.json, so loading either one tests the same data.
setCatalog(read("../exercise_catalog.json"));

test("lookup finds the area and muscles for a name", () => {
  for (const c of fixture("lookup_cases.json")) {
    const found = L.lookup(c.name);
    assert.equal(found ? found.group : null, c.group, c.name);
    assert.deepEqual(found ? found.muscles : [], c.muscles, c.name);
  }
});

test("exercise rollups match the golden cases", () => {
  for (const { case: name, exercise, expect: want } of fixture("exercise_cases.json")) {
    assert.equal(L.compactLine(exercise), want.compactLine, name);
    assert.equal(L.isCardio(exercise), want.isCardio, name);
    assert.deepEqual(L.musclesOf(exercise), want.muscles, name);
    assert.equal(L.groupOf(exercise), want.group, name);
    const top = L.topSet(exercise);
    for (const [field, value] of Object.entries(want.topSet)) {
      assert.deepEqual(top[field], value, `${name}: topSet.${field}`);
    }
  }
});

test("day rollups match the golden cases", () => {
  for (const { case: name, day, expect: want } of fixture("day_cases.json")) {
    const place = L.placeOf(day);
    assert.equal(place.place, want.place, name);
    assert.equal(place.guessed, want.placeGuessed, name);

    const totals = L.dayTotals(day);
    assert.equal(totals.exercises, want.totals.exercises, name);
    assert.equal(totals.sets, want.totals.sets, name);
    assert.equal(totals.strengthSets, want.totals.strengthSets, name);
    assert.equal(totals.reps.low, want.totals.repsLow, name);
    assert.equal(totals.reps.high, want.totals.repsHigh, name);
    assert.equal(totals.reps.hasRange, want.totals.hasRange, name);
    assert.equal(totals.cardioMinutes, want.totals.cardioMinutes, name);
    assert.equal(totals.cardioDistance, want.totals.cardioDistance, name);

    assert.deepEqual(L.setsPerMuscle([day]), want.setsPerMuscle, name);
    assert.deepEqual(L.missingAreas([day]), want.missingAreas, name);
    assert.equal(L.dayLoad(day), want.dayLoad, name);
    assert.equal(L.heatLevel(L.dayLoad(day)), want.heatLevel, name);
  }
});

test("formatting matches the golden cases", () => {
  const cases = fixture("format_cases.json");
  for (const c of cases.fmtNum) assert.equal(L.fmtNum(c.in), c.out, String(c.in));
  for (const c of cases.fmtHold) assert.equal(L.fmtHold(c.in), c.out, String(c.in));
  for (const c of cases.fmtReps) assert.equal(L.fmtReps(c.reps, c.repsMax), c.out, String(c.reps));
  for (const c of cases.dates) {
    assert.equal(L.fmtDateLong(c.date, c.today), c.long, c.date);
    assert.equal(L.fmtDateShort(c.date, c.today), c.short, c.date);
    assert.equal(L.fmtMonth(c.date), c.month, c.date);
  }
  for (const c of cases.changes) assert.equal(L.changeLabel(c.current, c.previous), c.out, c.out);
});

// Not in the fixtures, because only the frontend walks sessions this way.
test("sessions of one exercise come back oldest first, with changes", () => {
  const days = fixture("export_days.json");
  const sessions = L.exerciseSessions(days, "seated row");
  assert.ok(sessions.length >= 1);
  assert.deepEqual([...sessions].sort((a, b) => (a.date < b.date ? -1 : 1)).map((s) => s.date),
    sessions.map((s) => s.date));
  assert.equal(L.dominantKind(sessions), "weight");
  assert.equal(L.changeLabel(sessions[0].topSet, L.previousSession(sessions, 0)), "first");
});

test("an exercise with no sets has no top set and reads as not cardio", () => {
  assert.equal(L.topSet({ exercise: "Plank", sets: [] }), null);
  assert.equal(L.isCardio({ exercise: "Plank", sets: [] }), false);
  assert.equal(L.compactLine({ exercise: "Plank", sets: [] }), "");
});
