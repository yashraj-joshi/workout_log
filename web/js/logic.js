// Workout logic shared with the backend.
//
// Every function here has a line-for-line twin in
// backend/src/workoutlog/logic.py, and shared/fixtures/ runs against both so
// the two can't drift. Change one, change the other, and add a fixture case.
//
// These functions take plain objects (the records the API returns) so the
// JavaScript and Python versions read the same and share the fixture files.

import * as catalog from "./catalog.js";

export const EN_DASH = "–";
export const TIMES = "×";
export const MIDDOT = " · ";
export const MINUS = "−";

const MONTHS_LONG = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

// ---------------------------------------------------------------------------
// Name lookup
// ---------------------------------------------------------------------------

const NON_WORD = /[^a-z0-9]+/g;

// Lowercase word tokens. Hyphens split, so "push-up" == "push up".
export function normalizeWords(text) {
  return String(text || "").toLowerCase().replace(NON_WORD, " ").split(" ").filter(Boolean);
}

// True when `phrase` appears as a run of whole words inside `words`.
function containsPhrase(words, phrase) {
  if (!phrase.length || phrase.length > words.length) return false;
  for (let i = 0; i <= words.length - phrase.length; i++) {
    if (phrase.every((word, j) => words[i + j] === word)) return true;
  }
  return false;
}

// First matching rule wins, so the catalog's rule order is significant.
export function lookup(name) {
  const words = normalizeWords(name);
  if (!words.length) return null;
  for (const rule of catalog.lookupRules()) {
    for (const word of rule.words) {
      if (containsPhrase(words, normalizeWords(word))) {
        return { group: rule.group, muscles: [...rule.muscles] };
      }
    }
  }
  return null;
}

export function groupOf(exercise) {
  if (exercise.group) return exercise.group;
  const found = lookup(exercise.exercise || "");
  return found ? found.group : "Mobility";
}

// The exercise's own muscles (up to 4); otherwise look them up by name.
export function musclesOf(exercise) {
  const own = exercise.muscles || [];
  if (own.length) return own.slice(0, 4);
  const found = lookup(exercise.exercise || "");
  return found ? [...found.muscles] : [];
}

// ---------------------------------------------------------------------------
// Set and exercise shape
// ---------------------------------------------------------------------------

// Mirrors Python's _num: a real number, or nothing. Booleans are not numbers
// in JavaScript, so there is nothing extra to exclude here.
function num(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

// Group says Cardio, or every set is time/distance only.
export function isCardio(exercise) {
  if (groupOf(exercise) === "Cardio") return true;
  const sets = exercise.sets || [];
  if (!sets.length) return false;
  for (const s of sets) {
    const timed = num(s.minutes) !== null || num(s.distance) !== null;
    const other = ["reps", "weight", "seconds"].some((f) => num(s[f]) !== null);
    if (!timed || other) return false;
  }
  return true;
}

// Fields that must match for two sets to merge in the compact line.
export function setIdentity(s) {
  return JSON.stringify([
    s.reps ?? null, s.repsMax ?? null, s.weight ?? null,
    s.seconds ?? null, s.minutes ?? null,
    s.distance ?? null, s.distanceUnit || "mi",
    s.note || "",
  ]);
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

// One decimal at most, with thousands separators. 1.07 -> "1.1".
export function fmtNum(value) {
  if (value === null || value === undefined) return "—";
  const rounded = Math.round(Number(value) * 10) / 10;
  return rounded.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

export function fmtReps(reps, repsMax = null) {
  if (reps === null || reps === undefined) return "";
  if (repsMax !== null && repsMax !== undefined && repsMax !== reps) {
    return `${fmtNum(reps)}${EN_DASH}${fmtNum(repsMax)}`;
  }
  return fmtNum(reps);
}

// Under two minutes reads as seconds; longer reads as m:ss.
export function fmtHold(seconds) {
  if (seconds === null || seconds === undefined) return "";
  const total = Number(seconds);
  if (total < 120) return `${fmtNum(total)} s`;
  const whole = Math.round(total);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")} min`;
}

export function fmtWeight(weight, unit = "lb") {
  return `${fmtNum(weight)} ${unit || "lb"}`;
}

export function fmtDistance(distance, unit = "mi") {
  return `${fmtNum(distance)} ${unit || "mi"}`;
}

export function setLabel(s, unit = "lb") {
  const parts = [];
  if (num(s.reps) !== null) parts.push(fmtReps(s.reps, s.repsMax ?? null));
  if (num(s.seconds) !== null) parts.push(fmtHold(s.seconds));
  if (num(s.minutes) !== null) parts.push(`${fmtNum(s.minutes)} min`);
  if (num(s.distance) !== null) parts.push(fmtDistance(s.distance, s.distanceUnit));
  const base = parts.join(", ");
  if (num(s.weight) !== null) {
    const weight = fmtWeight(s.weight, unit);
    return base ? `${base} @ ${weight}` : weight;
  }
  return base;
}

// Consecutive identical sets merge: "3 × 10–12 @ 40 lb · 8 @ 30 lb".
export function compactLine(exercise) {
  const sets = exercise.sets || [];
  const unit = exercise.unit || "lb";
  const chunks = [];
  let index = 0;
  while (index < sets.length) {
    let run = 1;
    while (index + run < sets.length && setIdentity(sets[index + run]) === setIdentity(sets[index])) run++;
    const label = setLabel(sets[index], unit);
    if (label) chunks.push(run > 1 ? `${run} ${TIMES} ${label}` : label);
    index += run;
  }
  return chunks.join(MIDDOT);
}

// ---------------------------------------------------------------------------
// Top set
// ---------------------------------------------------------------------------

// Python's max() keeps the first of equal keys; so does this.
function bestBy(list, score) {
  let best = null;
  let bestScore = null;
  for (const item of list) {
    const key = score(item);
    if (best === null || compareKeys(key, bestScore) > 0) {
      best = item;
      bestScore = key;
    }
  }
  return best;
}

function compareKeys(a, b) {
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return a[i] > b[i] ? 1 : -1;
  }
  return 0;
}

// Heaviest set wins; ties go to more reps. With no weight anywhere, fall back
// to the longest hold, then the most minutes, then the most reps.
export function topSet(exercise) {
  const sets = exercise.sets || [];
  const unit = exercise.unit || "lb";

  const weighted = sets.filter((s) => num(s.weight) !== null);
  if (weighted.length) {
    const best = bestBy(weighted, (s) => [Number(s.weight), Number(s.reps || 0)]);
    const reps = fmtReps(best.reps ?? null, best.repsMax ?? null);
    let label = fmtWeight(best.weight, unit);
    if (reps) label = `${label} ${TIMES} ${reps}`;
    return { kind: "weight", value: Number(best.weight), unit,
      reps: best.reps ?? null, repsMax: best.repsMax ?? null, label };
  }

  const held = sets.filter((s) => num(s.seconds) !== null);
  if (held.length) {
    const best = bestBy(held, (s) => [Number(s.seconds)]);
    return { kind: "seconds", value: Number(best.seconds), unit: "sec",
      reps: null, repsMax: null, label: fmtHold(best.seconds) };
  }

  const timed = sets.filter((s) => num(s.minutes) !== null);
  if (timed.length) {
    const best = bestBy(timed, (s) => [Number(s.minutes)]);
    return { kind: "minutes", value: Number(best.minutes), unit: "min",
      reps: null, repsMax: null, label: `${fmtNum(best.minutes)} min` };
  }

  const repped = sets.filter((s) => num(s.reps) !== null);
  if (repped.length) {
    const best = bestBy(repped, (s) => [Number(s.reps)]);
    return { kind: "reps", value: Number(best.reps), unit: "reps",
      reps: best.reps ?? null, repsMax: best.repsMax ?? null,
      label: `${fmtReps(best.reps ?? null, best.repsMax ?? null)} reps` };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Day-level rollups
// ---------------------------------------------------------------------------

export function exercisesInOrder(day) {
  const exercises = (day || {}).exercises || {};
  return Object.keys(exercises).sort().map((key) => exercises[key]);
}

// An explicit place wins. Otherwise weighted strength work means gym.
export function placeOf(day) {
  const place = (day || {}).place;
  if (place === "gym" || place === "home") return { place, guessed: false };
  for (const ex of exercisesInOrder(day)) {
    if (isCardio(ex)) continue;
    if ((ex.sets || []).some((s) => num(s.weight) !== null)) return { place: "gym", guessed: true };
  }
  return { place: "home", guessed: true };
}

export function repsTotal(day) {
  let low = 0;
  let high = 0;
  let hasRange = false;
  for (const ex of exercisesInOrder(day)) {
    for (const s of ex.sets || []) {
      const reps = num(s.reps);
      if (reps === null) continue;
      const repsMax = num(s.repsMax);
      low += reps;
      high += repsMax !== null ? repsMax : reps;
      if (repsMax !== null && repsMax !== reps) hasRange = true;
    }
  }
  return { low, high, hasRange };
}

export function dayTotals(day) {
  const exercises = exercisesInOrder(day);
  const count = (ex) => (ex.sets || []).length;
  const cardio = cardioTotals([day]);
  return {
    exercises: exercises.length,
    sets: exercises.reduce((n, ex) => n + count(ex), 0),
    strengthSets: exercises.filter((ex) => !isCardio(ex)).reduce((n, ex) => n + count(ex), 0),
    reps: repsTotal(day),
    cardioMinutes: cardio.minutes,
    cardioDistance: cardio.distance,
  };
}

// Every set counts toward every muscle the exercise lists. Cardio exercises
// are left out; their minutes are reported separately.
export function setsPerMuscle(days) {
  const tally = new Map();
  for (const day of days) {
    for (const ex of exercisesInOrder(day)) {
      if (isCardio(ex)) continue;
      const count = (ex.sets || []).length;
      if (!count) continue;
      for (const muscle of musclesOf(ex)) {
        if (muscle === "Cardio") continue;
        tally.set(muscle, (tally.get(muscle) || 0) + count);
      }
    }
  }
  const rows = [...tally].map(([muscle, sets]) => ({ muscle, sets }));
  // Most sets first, then by name. Compared the way Python compares strings.
  rows.sort((a, b) => b.sets - a.sets || (a.muscle < b.muscle ? -1 : a.muscle > b.muscle ? 1 : 0));
  return rows;
}

export function cardioTotals(days) {
  let minutes = 0;
  let distance = 0;
  for (const day of days) {
    for (const ex of exercisesInOrder(day)) {
      if (!isCardio(ex)) continue;
      for (const s of ex.sets || []) {
        minutes += num(s.minutes) || 0;
        distance += num(s.distance) || 0;
      }
    }
  }
  const round4 = (n) => Math.round(n * 1e4) / 1e4;
  return { minutes: round4(minutes), distance: round4(distance) };
}

// Heatmap weight for a day: a cardio exercise counts as one set.
export function dayLoad(day) {
  return exercisesInOrder(day).reduce((total, ex) => total + (isCardio(ex) ? 1 : (ex.sets || []).length), 0);
}

export function heatLevel(load) {
  if (load <= 0) return 0;
  if (load <= 5) return 1;
  if (load <= 11) return 2;
  return 3;
}

export function missingAreas(days) {
  const worked = new Set(setsPerMuscle(days).map((row) => row.muscle));
  return catalog.mainAreas().filter((a) => !a.muscles.some((m) => worked.has(m))).map((a) => a.label);
}

// ---------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------

// A calendar date, never a timestamp: built in UTC so the local timezone can
// never shift it to the day before.
export function parseDate(value) {
  const [year, month, day] = String(value).split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

export function fmtDateLong(value, today = null) {
  const d = parseDate(value);
  let text = `${WEEKDAYS[d.getUTCDay()]}, ${MONTHS_LONG[d.getUTCMonth()]} ${d.getUTCDate()}`;
  if (today && parseDate(today).getUTCFullYear() !== d.getUTCFullYear()) text += `, ${d.getUTCFullYear()}`;
  return text;
}

// The calendar's month heading: "September 2026".
export function fmtMonth(value) {
  const d = parseDate(value);
  return `${MONTHS_LONG[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

export function fmtDateShort(value, today = null) {
  const d = parseDate(value);
  let text = `${MONTHS_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}`;
  if (today && parseDate(today).getUTCFullYear() !== d.getUTCFullYear()) text += `, ${d.getUTCFullYear()}`;
  return text;
}

// ---------------------------------------------------------------------------
// Sessions of one exercise across days (Progress tab, and the day summary)
// ---------------------------------------------------------------------------

// Names match on words, so "Seated Row" and "seated row" are one exercise.
export function sameExercise(a, b) {
  const left = normalizeWords(a);
  const right = normalizeWords(b);
  return left.length === right.length && left.every((word, i) => word === right[i]);
}

// Oldest first, one entry per day that contains the exercise.
export function exerciseSessions(days, name) {
  const out = [];
  for (const day of days) {
    for (const ex of exercisesInOrder(day)) {
      if (sameExercise(ex.exercise || "", name)) {
        out.push({ date: day.date, exercise: ex, topSet: topSet(ex) });
      }
    }
  }
  out.sort((a, b) => ((a.date || "") < (b.date || "") ? -1 : (a.date || "") > (b.date || "") ? 1 : 0));
  return out;
}

// The chart plots the kind most sessions have, so one odd session does not
// change the axis.
export function dominantKind(sessions) {
  const counts = new Map();
  for (const row of sessions) {
    if (row.topSet) counts.set(row.topSet.kind, (counts.get(row.topSet.kind) || 0) + 1);
  }
  if (!counts.size) return null;
  return [...counts].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0][0];
}

// The newest earlier session whose top set is the same kind.
export function previousSession(sessions, index) {
  const current = sessions[index].topSet;
  if (!current) return null;
  for (let earlier = index - 1; earlier >= 0; earlier--) {
    const top = sessions[earlier].topSet;
    if (top && top.kind === current.kind) return sessions[earlier];
  }
  return null;
}

// "+5 lb", "−2 reps", "same" or "first".
export function changeLabel(current, previous) {
  if (!current) return "";
  if (!previous || previous.kind !== current.kind) return "first";

  const signed = (delta, unit) => `${delta > 0 ? "+" : MINUS}${fmtNum(Math.abs(delta))} ${unit}`;
  const unit = { weight: current.unit || "lb", seconds: "sec", minutes: "min", reps: "reps" }[current.kind];
  const delta = current.value - previous.value;
  if (delta) return signed(delta, unit);
  if (current.kind === "weight") {
    const repDelta = Number(current.reps || 0) - Number(previous.reps || 0);
    if (repDelta) return signed(repDelta, "reps");
  }
  return "same";
}
