// What the Add/Edit dialog does with typed input: the reps formats I actually
// use, the exact error wording from the brief, and the limits that stop a
// request the server would only reject anyway.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { setCatalog, commonNames } from "../js/catalog.js";
import {
  buildExercise, buildSets, cleanName, matchNames, nameSuggestions,
  parseMuscles, parseNumber, parseReps, rowsFromSets, typeForName, typeOfSets,
} from "../js/parse.js";

setCatalog(JSON.parse(readFileSync(fileURLToPath(new URL("../exercise_catalog.json", import.meta.url)), "utf8")));

const weights = (rows) => buildSets("weights", rows);

test("reps accept a plain count and every range I type", () => {
  assert.deepEqual(parseReps("12"), { reps: 12 });
  for (const text of ["10-12", "10–12", "10—12", "10 to 12", "10to12", " 10 - 12 "]) {
    assert.deepEqual(parseReps(text), { reps: 10, repsMax: 12 }, text);
  }
  // A range with the same number on both sides is just that number.
  assert.deepEqual(parseReps("10-10"), { reps: 10 });
  assert.deepEqual(parseReps(""), { blank: true });
  for (const text of ["ten", "12x", "12-", "1.5", "12-10", "0", "1001"]) {
    assert.deepEqual(parseReps(text), { bad: true }, text);
  }
});

test("numbers take decimals but not junk, and respect their limits", () => {
  assert.deepEqual(parseNumber("40"), { value: 40 });
  assert.deepEqual(parseNumber("1.3"), { value: 1.3 });
  assert.deepEqual(parseNumber(".5"), { value: 0.5 });
  assert.deepEqual(parseNumber("  "), { blank: true });
  for (const text of ["40 lb", "-5", "4,0", "e5", ""]) {
    assert.ok(parseNumber(text).value === undefined, text);
  }
  assert.deepEqual(parseNumber("3000", { max: 2000 }), { bad: true });
  assert.deepEqual(parseNumber("1.5", { whole: true }), { bad: true });
});

test("muscles are canonicalised, de-duplicated, capped at four, and unknowns named", () => {
  assert.deepEqual(parseMuscles("chest, TRICEPS , front shoulders"),
    { muscles: ["Chest", "Triceps", "Front shoulders"], unknown: [] });
  assert.deepEqual(parseMuscles("Chest, chest").muscles, ["Chest"]);
  assert.equal(parseMuscles("Chest, Triceps, Lats, Core, Glutes").muscles.length, 4);
  assert.deepEqual(parseMuscles("Chest, pecs").unknown, ["pecs"]);
  assert.deepEqual(parseMuscles("").muscles, []);
});

test("set rows become sets, with blank rows dropped", () => {
  assert.deepEqual(weights([{ reps: "10", weight: "40" }]).sets, [{ reps: 10, weight: 40 }]);
  assert.deepEqual(weights([{ reps: "10", weight: "40" }, { reps: "", weight: "" }]).sets,
    [{ reps: 10, weight: 40 }]);
  // Bodyweight work has reps and no weight; a weight with no reps is a hold-ish
  // single, and both are valid.
  assert.deepEqual(weights([{ reps: "12", weight: "" }]).sets, [{ reps: 12 }]);
  assert.deepEqual(weights([{ reps: "", weight: "45" }]).sets, [{ weight: 45 }]);
  assert.deepEqual(buildSets("hold", [{ seconds: "30", note: "each side" }]).sets,
    [{ seconds: 30, note: "each side" }]);
  assert.deepEqual(buildSets("time", [{ minutes: "25", distance: "1.3" }]).sets,
    [{ minutes: 25, distance: 1.3, distanceUnit: "mi" }]);
  assert.deepEqual(buildSets("time", [{ minutes: "25", distance: "" }]).sets, [{ minutes: 25 }]);
});

test("the error messages are the ones the brief specifies", () => {
  assert.equal(weights([{ reps: "ten", weight: "40" }]).error,
    "Set 1: reps should be a number like 12, or a range like 10-12.");
  assert.equal(weights([{ reps: "10", weight: "heavy" }]).error,
    "Set 1: weight should be a number like 40.");
  assert.equal(weights([{ reps: "10", weight: "40" }, { reps: "x", weight: "" }]).error,
    "Set 2: reps should be a number like 12, or a range like 10-12.");
  assert.equal(buildSets("time", [{ minutes: "25", distance: "far" }]).error,
    "Set 1: minutes and distance should be numbers.");
  assert.equal(buildSets("hold", [{ seconds: "a while" }]).error,
    "Set 1: seconds should be a number like 30.");
  assert.equal(weights([]).error, "Fill in at least one set.");
  assert.equal(weights([{ reps: "", weight: "", note: "" }]).error, "Fill in at least one set.");
  // A note with nothing measured is not a set.
  assert.equal(weights([{ reps: "", weight: "", note: "felt easy" }]).error, "Fill in at least one set.");

  assert.equal(buildExercise({ name: "  ", date: "2026-10-07", type: "weights", rows: [] }).error,
    "Enter the exercise name.");
  for (const date of ["", "tomorrow", "2026-10-7"]) {
    assert.equal(buildExercise({ name: "Seated row", date, type: "weights", rows: [{ reps: "10" }] }).error,
      "Pick a date.", date);
  }
});

test("a whole exercise comes out ready to send", () => {
  const built = buildExercise({
    name: "  dumbbell   bench press ",
    date: "2026-10-07",
    group: "Chest",
    muscles: "chest, triceps",
    type: "weights",
    perHand: true,
    notes: "  Last set felt heavy.  ",
    rows: [{ reps: "10", weight: "25" }, { reps: "8", weight: "30", note: "slow" }],
  });
  assert.deepEqual(built, {
    date: "2026-10-07",
    exercise: {
      exercise: "dumbbell bench press",
      unit: "lb",
      sets: [{ reps: 10, weight: 25 }, { reps: 8, weight: 30, note: "slow" }],
      group: "Chest",
      muscles: ["Chest", "Triceps"],
      perHand: true,
      notes: "Last set felt heavy.",
    },
  });
});

test("per-dumbbell is dropped for time and hold work, where it means nothing", () => {
  const built = buildExercise({
    name: "Plank", date: "2026-10-07", type: "hold", perHand: true, rows: [{ seconds: "45" }],
  });
  assert.equal(built.exercise.perHand, undefined);
});

test("an unknown muscle is refused by name rather than silently dropped", () => {
  const built = buildExercise({
    name: "Seated row", date: "2026-10-07", muscles: "pecs", type: "weights", rows: [{ reps: "10" }],
  });
  assert.match(built.error, /^Not a muscle we know: pecs\./);
});

test("editing an exercise round-trips its sets back through the dialog", () => {
  for (const [type, sets] of [
    ["weights", [{ reps: 10, repsMax: 12, weight: 40 }, { reps: 8, weight: 45, note: "slow" }]],
    ["hold", [{ seconds: 30, note: "each side" }]],
    ["time", [{ minutes: 25, distance: 1.3, distanceUnit: "mi" }]],
    ["weights", [{ reps: 12 }]],
  ]) {
    assert.equal(typeOfSets(sets), type, JSON.stringify(sets));
    // Rows out, rows back in: what the dialog shows must save as what it read.
    assert.deepEqual(buildSets(type, rowsFromSets(sets)).sets, sets, JSON.stringify(sets));
  }
});

test("a reps range round-trips as a plain hyphen, and 10-10 collapses", () => {
  assert.equal(rowsFromSets([{ reps: 10, repsMax: 12 }])[0].reps, "10-12");
  assert.equal(rowsFromSets([{ reps: 10, repsMax: 10 }])[0].reps, "10");
  assert.equal(rowsFromSets([{ weight: 45 }])[0].reps, "");
});

test("the type starts on Time for a cardio name and Weights otherwise", () => {
  assert.equal(typeForName("Treadmill walk"), "time");
  assert.equal(typeForName("elliptical"), "time");
  assert.equal(typeForName("Seated row"), "weights");
  assert.equal(typeForName(""), "weights");
});

test("cleanName collapses the whitespace I leave behind", () => {
  assert.equal(cleanName("  seated   row\n"), "seated row");
  assert.equal(cleanName(undefined), "");
});

test("name suggestions put my own names first, most recent first", () => {
  const days = [
    { date: "2026-10-01", exercises: { "01": { exercise: "Seated row" } } },
    { date: "2026-10-05", exercises: { "01": { exercise: "Lat pulldown" }, "02": { exercise: "seated row" } } },
  ];
  const names = nameSuggestions(days, commonNames());
  assert.deepEqual(names.slice(0, 2), ["Lat pulldown", "seated row"]);
  // One entry per exercise, whatever the capitalisation, and the common names
  // I have already used are not repeated below.
  assert.equal(names.filter((n) => n.toLowerCase() === "seated row").length, 1);
  assert.ok(names.includes("Goblet squat"));
});

test("name matches wait for something to be typed", () => {
  assert.deepEqual(matchNames("", commonNames()), []);
  assert.deepEqual(matchNames("  - ", commonNames()), []);
});

test("a name that starts with what I typed comes before one that only contains it", () => {
  const names = ["Bicep curl", "Curl bar row", "Leg curl"];
  assert.deepEqual(matchNames("curl", names), ["Curl bar row", "Bicep curl", "Leg curl"]);
  // Every word I type has to start a word in the name, in any order.
  assert.deepEqual(matchNames("inc bench", commonNames()), ["Incline bench press"]);
  assert.deepEqual(matchNames("db row", commonNames()), []);
});

test("my own names stay above the common ones in the same tier", () => {
  const names = ["Seated row", "Lat pulldown", "Seated leg curl", "Seated calf raise"];
  assert.deepEqual(matchNames("seated", names), ["Seated row", "Seated leg curl", "Seated calf raise"]);
});

test("spaces and hyphens do not stop a match", () => {
  assert.ok(matchNames("pushup", commonNames()).includes("Push-up"));
  assert.ok(matchNames("pull-up", commonNames()).includes("Pull-up"));
});

test("name matches stop at the limit", () => {
  assert.equal(matchNames("c", commonNames()).length, 8);
  assert.equal(matchNames("c", commonNames(), 3).length, 3);
});

test("no list when the only match is already typed in full", () => {
  assert.deepEqual(matchNames("leg extension", ["Leg extension", "Leg press"]), []);
  assert.deepEqual(matchNames("leg", ["Leg extension"]), ["Leg extension"]);
});
