// The CSV the browser builds must be byte-for-byte what the backend builds.
// Both are checked against shared/fixtures/export_expected.csv, so a change to
// either one without the other turns a suite red.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { setCatalog } from "../js/catalog.js";
import { HEADER, build, filename, rowsFor } from "../js/csv.js";

const read = (path) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");
setCatalog(JSON.parse(read("../exercise_catalog.json")));

const days = JSON.parse(read("../../shared/fixtures/export_days.json"));
const expected = read("../../shared/fixtures/export_expected.csv");
const column = Object.fromEntries(HEADER.map((name, i) => [name, i]));

test("the file matches the golden export, byte for byte", () => {
  assert.equal(build(days), expected);
});

test("one row per set, oldest day first", () => {
  const rows = rowsFor(days);
  const sets = days.reduce((n, day) =>
    n + Object.values(day.exercises || {}).reduce((m, ex) => m + (ex.sets || []).length, 0), 0);
  assert.equal(rows.length, sets);
  const dates = rows.map((r) => r[column.Date]);
  assert.deepEqual(dates, [...dates].sort());
});

test("the exercise note and the logged time appear only on the first row of each exercise", () => {
  const rows = rowsFor(days).filter((r) => r[column.Exercise] === "Seated row");
  assert.equal(rows[0][column["Exercise note"]], "Felt good, no back pain.");
  assert.ok(rows[0][column["Logged at"]]);
  assert.equal(rows[1][column["Exercise note"]], "");
  assert.equal(rows[1][column["Logged at"]], "");
});

test("a unit is written only where there is something to measure", () => {
  const walk = rowsFor(days).find((r) => r[column.Exercise] === "Treadmill walk");
  assert.equal(walk[column.Unit], "", "no weight, so no unit");
  assert.equal(walk[column["Distance unit"]], "mi");
  const row = rowsFor(days).find((r) => r[column.Weight]);
  assert.equal(row[column.Unit], "lb");
});

test("commas, quotes and newlines in my own text are quoted the standard way", () => {
  const built = build([{
    date: "2026-10-05",
    exercises: {
      "01": {
        order: 1, exercise: "Seated row", group: "Back", muscles: ["Mid back"], unit: "lb",
        notes: 'Felt good, "strong"\neven late on',
        sets: [{ reps: 10, weight: 40, note: "each side, slow" }],
      },
    },
  }]);
  // Byte for byte what Python's csv.writer produces for the same day: the
  // field is wrapped, inner quotes are doubled, and the newline stays inside.
  assert.equal(built,
    HEADER.join(",") + "\n"
    + '2026-10-05,1,Seated row,Back,Mid back,1,10,,40,lb,,,,,,"each side, slow",'
    + '"Felt good, ""strong""\neven late on",\n');
});

test("the file is named for the day it was exported", () => {
  assert.equal(filename("2026-10-07"), "workout-log-2026-10-07.csv");
});

test("muscles are joined with a semicolon so the commas stay the CSV's", () => {
  const row = rowsFor(days)[0];
  assert.ok(row[column.Muscles].includes("; "));
  assert.ok(!row[column.Muscles].includes(","));
});
