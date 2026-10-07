// The parts of the Trends and Progress tabs that are arithmetic rather than
// DOM: which window a range covers, how the heatmap is laid out, which
// exercises the picker offers, and where the chart's axis ticks land.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { setCatalog } from "../js/catalog.js";
import { RANGES, heatWeeks, windowFor } from "../js/views/trends.js";
import { changeTone, exerciseList, ticksFor } from "../js/views/progress.js";

setCatalog(JSON.parse(readFileSync(fileURLToPath(new URL("../exercise_catalog.json", import.meta.url)), "utf8")));

const TODAY = "2026-10-07";
const day = (date) => ({ date, exercises: { "01": { order: 1, exercise: "Seated row", group: "Back", muscles: ["Mid back"], unit: "lb", sets: [{ reps: 10, weight: 40 }] } } });

test("each range covers the right window, counting today as day one", () => {
  assert.deepEqual(windowFor("7", [], TODAY), { from: "2026-10-01", to: TODAY, range: RANGES[0] });
  assert.equal(windowFor("30", [], TODAY).from, "2026-09-08");
  assert.equal(windowFor("90", [], TODAY).from, "2026-07-10");
  assert.equal(windowFor("365", [], TODAY).from, "2025-10-08");
});

test("All starts at the first logged day, not at the beginning of time", () => {
  const w = windowFor("all", [day("2026-09-28"), day("2026-10-05")], TODAY);
  assert.equal(w.from, "2026-09-28");
  assert.equal(w.to, TODAY);
  // With nothing logged there is no span to show, so it collapses to today.
  assert.equal(windowFor("all", [], TODAY).from, TODAY);
});

test("an unknown range falls back to 30 days rather than breaking", () => {
  assert.equal(windowFor("nonsense", [], TODAY).range.key, "30");
});

test("the heatmap is whole Sunday-to-Saturday columns, padded outside the window", () => {
  const weeks = heatWeeks("2026-10-01", "2026-10-07"); // Thursday to Wednesday
  assert.ok(weeks.every((w) => w.length === 7));
  // The first column starts on the Sunday before the window, so the days
  // before it are blanks rather than dates from outside the range.
  assert.deepEqual(weeks[0].slice(0, 4), [null, null, null, null]);
  assert.equal(weeks[0][4], "2026-10-01");
  const dates = weeks.flat().filter(Boolean);
  assert.equal(dates[0], "2026-10-01");
  assert.equal(dates[dates.length - 1], "2026-10-07");
  assert.equal(dates.length, 7);
});

test("a year of heatmap is 53 columns at most", () => {
  const { from, to } = windowFor("365", [], TODAY);
  const weeks = heatWeeks(from, to);
  assert.ok(weeks.length <= 54, `${weeks.length} columns`);
  assert.equal(weeks.flat().filter(Boolean).length, 365);
});

test("the exercise picker lists each exercise once, most recently done first", () => {
  const days = [
    { date: "2026-10-01", exercises: { "01": { exercise: "Seated row", sets: [{ reps: 10 }] } } },
    { date: "2026-10-05", exercises: {
      "01": { exercise: "Lat pulldown", sets: [{ reps: 10 }] },
      "02": { exercise: "seated row", sets: [{ reps: 10 }] },
    } },
  ];
  const list = exerciseList(days);
  assert.deepEqual(list.map((r) => r.name), ["Lat pulldown", "seated row"]);
  // Counted across days whatever the capitalisation, and the latest spelling wins.
  assert.deepEqual(list.map((r) => r.sessions), [1, 2]);
  assert.equal(list[1].last, "2026-10-05");
});

test("axis ticks land on round numbers that cover the data", () => {
  for (const [min, max] of [[35, 70], [0, 12], [7.5, 9], [100, 450]]) {
    const ticks = ticksFor(min, max);
    assert.ok(ticks.length >= 2 && ticks.length <= 8, `${min}-${max}: ${ticks}`);
    assert.ok(ticks[0] <= min, `${ticks[0]} should sit at or below ${min}`);
    assert.ok(ticks[ticks.length - 1] >= max, `${ticks.at(-1)} should sit at or above ${max}`);
    const step = Math.round((ticks[1] - ticks[0]) * 100) / 100;
    assert.ok(ticks.every((t, i) => i === 0 || Math.abs(t - ticks[i - 1] - step) < 1e-6), `even spacing: ${ticks}`);
  }
});

test("one session, or a flat line, still gives a usable axis", () => {
  assert.deepEqual(ticksFor(40, 40), [40]);
  assert.ok(ticksFor(0, 0).length === 1);
});

test("a change reads good when it went up and bad when it went down", () => {
  assert.equal(changeTone("+5 lb"), "good");
  assert.equal(changeTone("−2 reps"), "bad");
  assert.equal(changeTone("same"), "faint");
  assert.equal(changeTone("first"), "faint");
});
