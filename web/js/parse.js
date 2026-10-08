// Turning what I type in the Add/Edit dialog into the records the API accepts.
//
// Kept apart from the dialog itself so it can be tested without a browser, and
// so the limits here stay next to each other. They are the same limits
// models.py enforces: the server is the authority, this just means a typo is
// caught before a request goes out.

import { lookup, normalizeWords } from "./logic.js";
import { canonicalMuscle } from "./catalog.js";

export const TYPES = ["weights", "time", "hold"];

const LIMITS = {
  reps: { min: 1, max: 1000, whole: true },
  weight: { min: 0, max: 2000 },
  seconds: { min: 0.1, max: 7200 },
  minutes: { min: 0.1, max: 600 },
  distance: { min: 0, max: 1000 },
};

const NUMBER = /^\d*\.?\d+$/;
// "10-12", "10–12" (en dash), "10—12" (em dash) and "10 to 12" all mean a range.
const RANGE = /^(\d+)\s*(?:-|–|—|to)\s*(\d+)$/i;

// "" -> {blank}, "40" -> {value}, anything else -> {bad}.
export function parseNumber(text, limit = {}) {
  const cleaned = String(text ?? "").trim();
  if (!cleaned) return { blank: true };
  if (!NUMBER.test(cleaned)) return { bad: true };
  const value = Number(cleaned);
  if (!Number.isFinite(value)) return { bad: true };
  if (limit.whole && !Number.isInteger(value)) return { bad: true };
  if (limit.min !== undefined && value < limit.min) return { bad: true };
  if (limit.max !== undefined && value > limit.max) return { bad: true };
  return { value };
}

// "" -> {blank}, "12" -> {reps}, "10 to 12" -> {reps, repsMax}.
export function parseReps(text) {
  const cleaned = String(text ?? "").trim();
  if (!cleaned) return { blank: true };
  const range = cleaned.match(RANGE);
  if (range) {
    const low = parseNumber(range[1], LIMITS.reps);
    const high = parseNumber(range[2], LIMITS.reps);
    if (low.bad || high.bad || high.value < low.value) return { bad: true };
    // "10-10" is not a range, so it is stored as a plain 10.
    return high.value === low.value ? { reps: low.value } : { reps: low.value, repsMax: high.value };
  }
  const single = parseNumber(cleaned, LIMITS.reps);
  return single.bad ? { bad: true } : { reps: single.value };
}

// Comma separated, main one first, canonicalised against the vocabulary.
// Unknown names come back in `unknown` so the dialog can name them.
export function parseMuscles(text) {
  const muscles = [];
  const unknown = [];
  for (const part of String(text ?? "").split(",")) {
    const name = part.trim();
    if (!name) continue;
    const canonical = canonicalMuscle(name);
    if (!canonical) unknown.push(name);
    else if (!muscles.includes(canonical)) muscles.push(canonical);
  }
  return { muscles: muscles.slice(0, 4), unknown };
}

// Which set fields a name suggests, so adding "Treadmill walk" starts on Time.
export function typeForName(name) {
  const found = lookup(name);
  return found && found.group === "Cardio" ? "time" : "weights";
}

export function cleanName(text) {
  return String(text ?? "").trim().split(/\s+/).join(" ");
}

// A row the person left completely alone. Blank rows are dropped rather than
// rejected, so "+ Add set" and then Save does the obvious thing.
function rowIsBlank(row, type) {
  const fields = type === "weights" ? ["reps", "weight"] : type === "time" ? ["minutes", "distance"] : ["seconds"];
  return fields.every((f) => !String(row[f] ?? "").trim()) && !String(row.note ?? "").trim();
}

// Returns {sets} or {error}. `error` is shown as-is, so the wording here is
// the wording in section 9 of the brief.
export function buildSets(type, rows) {
  const sets = [];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const label = `Set ${i + 1}`;
    if (rowIsBlank(row, type)) continue;
    const set = {};

    if (type === "weights") {
      const reps = parseReps(row.reps);
      if (reps.bad) return { error: `${label}: reps should be a number like 12, or a range like 10-12.` };
      if (!reps.blank) {
        set.reps = reps.reps;
        if (reps.repsMax !== undefined) set.repsMax = reps.repsMax;
      }
      const weight = parseNumber(row.weight, LIMITS.weight);
      if (weight.bad) return { error: `${label}: weight should be a number like 40.` };
      if (!weight.blank) set.weight = weight.value;
    } else if (type === "time") {
      const minutes = parseNumber(row.minutes, LIMITS.minutes);
      const distance = parseNumber(row.distance, LIMITS.distance);
      if (minutes.bad || distance.bad) return { error: `${label}: minutes and distance should be numbers.` };
      if (!minutes.blank) set.minutes = minutes.value;
      if (!distance.blank) {
        set.distance = distance.value;
        set.distanceUnit = "mi";
      }
    } else {
      const seconds = parseNumber(row.seconds, LIMITS.seconds);
      if (seconds.bad) return { error: `${label}: seconds should be a number like 30.` };
      if (!seconds.blank) set.seconds = seconds.value;
    }

    // Every set needs something measured; a bare note is not a set.
    if (!["reps", "weight", "seconds", "minutes", "distance"].some((f) => set[f] !== undefined)) continue;

    const note = String(row.note ?? "").trim();
    if (note) set.note = note.slice(0, 80);
    sets.push(set);
  }
  if (!sets.length) return { error: "Fill in at least one set." };
  return { sets };
}

// The whole dialog, validated in one place. Returns {date, exercise} ready to
// POST or PUT, or {error}.
export function buildExercise(form) {
  const name = cleanName(form.name);
  if (!name) return { error: "Enter the exercise name." };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(form.date || ""))) return { error: "Pick a date." };

  const built = buildSets(form.type, form.rows || []);
  if (built.error) return { error: built.error };

  const { muscles, unknown } = parseMuscles(form.muscles);
  if (unknown.length) {
    return { error: `Not a muscle we know: ${unknown[0]}. Clear the field to fill it in automatically.` };
  }

  const exercise = { exercise: name, unit: form.unit || "lb", sets: built.sets };
  if (form.group) exercise.group = form.group;
  if (muscles.length) exercise.muscles = muscles;
  // perHand is only meaningful for weights, and only when true.
  if (form.perHand && form.type === "weights") exercise.perHand = true;
  const notes = String(form.notes ?? "").trim();
  if (notes) exercise.notes = notes.slice(0, 300);
  return { date: form.date, exercise };
}

// Which set fields an exercise already uses, so Edit opens on the right type.
export function typeOfSets(sets) {
  const has = (field) => (sets || []).some((s) => s[field] !== undefined && s[field] !== null);
  if (has("seconds")) return "hold";
  if (has("minutes") || has("distance")) return "time";
  return "weights";
}

// Stored sets back into the dialog's text rows, so Edit starts from what is
// already logged. A range is written with a plain hyphen: easier to retype.
export function rowsFromSets(sets) {
  const text = (value) => (value === undefined || value === null ? "" : String(value));
  return (sets || []).map((s) => ({
    reps: s.repsMax !== undefined && s.repsMax !== null && s.repsMax !== s.reps
      ? `${s.reps}-${s.repsMax}` : text(s.reps),
    weight: text(s.weight),
    minutes: text(s.minutes),
    distance: text(s.distance),
    seconds: text(s.seconds),
    note: text(s.note),
  }));
}

// Suggestions for the name field: my own names, most recent first, then the
// common ones I haven't used.
export function nameSuggestions(days, commonNames) {
  const seen = new Map();
  for (const day of [...days].sort((a, b) => (a.date > b.date ? -1 : 1))) {
    for (const ex of Object.values(day.exercises || {})) {
      const key = normalizeWords(ex.exercise || "").join(" ");
      if (key && !seen.has(key)) seen.set(key, ex.exercise);
    }
  }
  const mine = [...seen.values()];
  const rest = commonNames.filter((name) => !seen.has(normalizeWords(name).join(" ")));
  return [...mine, ...rest];
}

// The names to offer under the name field as I type, best first. Starting with
// what I typed beats containing it, which beats matching once the spaces and
// hyphens are gone ("pushup" finds Push-up). Within each tier the order of
// `names` is kept, so my own recent names stay above the common ones.
export function matchNames(query, names, limit = 8) {
  const typed = normalizeWords(query);
  if (!typed.length) return [];
  const key = typed.join(" ");
  const squashed = typed.join("");
  const tiers = [[], [], []];
  for (const name of names) {
    const words = normalizeWords(name);
    if (words.join(" ").startsWith(key)) tiers[0].push(name);
    else if (typed.every((part) => words.some((word) => word.startsWith(part)))) tiers[1].push(name);
    else if (words.join("").includes(squashed)) tiers[2].push(name);
  }
  const found = tiers.flat().slice(0, limit);
  // Nothing to suggest when the only match is what is already in the field.
  if (found.length === 1 && normalizeWords(found[0]).join(" ") === key) return [];
  return found;
}
