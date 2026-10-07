// The Day tab: the calendar, the day's exercises, the stat tiles, the editable
// summary, notes and bodyweight, and the gym/home switch.
//
// Everything is rebuilt from the log each time the data changes, so there is
// no second copy of the truth to keep in step. Two things survive a rebuild:
// what I have half-typed into a text box (`drafts`), and where the caret was.

import { h, replace, toast } from "../dom.js";
import * as L from "../logic.js";
import { parseNumber } from "../parse.js";

const MAX_SUMMARY = 600;
const WEEKDAY_INITIALS = ["S", "M", "T", "W", "T", "F", "S"];
const pad = (n) => String(n).padStart(2, "0");
const iso = (year, month, day) => `${year}-${pad(month)}-${pad(day)}`;

// The weeks of one month as rows of 7, blanks padding the ends.
export function monthGrid(year, month) {
  const offset = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  const length = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const cells = [...Array(offset).fill(null), ...Array.from({ length }, (_, i) => iso(year, month, i + 1))];
  while (cells.length % 7) cells.push(null);
  return Array.from({ length: cells.length / 7 }, (_, w) => cells.slice(w * 7, w * 7 + 7));
}

// The day to open on: today if it has entries, otherwise the most recent day
// that does.
export function firstSelection(days, today) {
  const logged = days.filter((d) => L.exercisesInOrder(d).length).map((d) => d.date).sort();
  if (logged.includes(today)) return today;
  const past = logged.filter((d) => d <= today);
  return (past.length ? past[past.length - 1] : logged[logged.length - 1]) || null;
}

// "Logged 2:33 PM – 3:40 PM", or one time when they match.
function loggedRange(exercises) {
  const stamps = exercises.map((ex) => ex.loggedAt).filter(Boolean).sort();
  if (!stamps.length) return null;
  const clock = (value) => {
    const when = new Date(value);
    return Number.isNaN(when.getTime())
      ? null : when.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  };
  const first = clock(stamps[0]);
  const last = clock(stamps[stamps.length - 1]);
  if (!first || !last) return null;
  return first === last ? `Logged ${first}` : `Logged ${first} ${L.EN_DASH} ${last}`;
}

export function createDayTab({ root, actions, today }) {
  let days = [];
  let selected = null;
  let month = null; // {year, month} on show in the calendar
  let drafts = {}; // field -> what I have typed but not saved
  let pendingRemove = null; // the exercise key showing "Remove this exercise?"

  const byDate = () => new Map(days.map((d) => [d.date, d]));
  const dayFor = (date) => (date ? byDate().get(date) || null : null);
  // [key, exercise] in order. The key is what the API routes take, so it comes
  // from the map rather than from the exercise's own `order`.
  const entriesOf = (day) => Object.keys((day || {}).exercises || {}).sort()
    .map((key) => [key, day.exercises[key]]);

  function setSelected(date) {
    if (date === selected) return;
    selected = date;
    drafts = {};
    pendingRemove = null;
    month = date ? { year: Number(date.slice(0, 4)), month: Number(date.slice(5, 7)) } : month;
    render();
  }

  function update(nextDays, nextToday) {
    days = nextDays || [];
    if (nextToday) today = nextToday;
    if (!selected) {
      const first = firstSelection(days, today);
      if (first) {
        selected = first;
        month = { year: Number(first.slice(0, 4)), month: Number(first.slice(5, 7)) };
      }
    }
    if (!month) month = { year: Number(today.slice(0, 4)), month: Number(today.slice(5, 7)) };
    render();
  }

  // --------------------------------------------------------------- saving

  // Every write returns the day it changed, which goes straight into the log.
  async function save(work, { field, done }) {
    try {
      const body = await work();
      if (body && "day" in body) actions.applyDay(selected, body.day);
      if (field) delete drafts[field];
      if (done) toast(done);
      actions.refresh();
      render();
    } catch (err) {
      toast(err.status === 0 ? "You're offline. This needs a connection." : err.message);
    }
  }

  const patch = (changes, done, field) =>
    save(() => actions.api.patchDay(selected, changes), { field, done });

  // ------------------------------------------------------------ rendering

  // Rebuilding the tab moves focus and the caret, so put them back.
  function render() {
    const active = document.activeElement;
    const id = active && root.contains(active) ? active.id : null;
    const caret = id && "selectionStart" in active ? active.selectionStart : null;
    replace(root, body());
    if (!id) return;
    const again = root.querySelector(`#${id}`);
    if (!again) return;
    again.focus({ preventScroll: true });
    if (caret !== null && "setSelectionRange" in again) {
      try { again.setSelectionRange(caret, caret); } catch { /* not a text field any more */ }
    }
  }

  function body() {
    const day = dayFor(selected);
    const exercises = L.exercisesInOrder(day);
    const nothingAnywhere = !days.some((d) => L.exercisesInOrder(d).length);

    return h("div", { class: "day-grid" },
      h("div", { class: "col" },
        calendarCard(),
        h("button", { class: "btn primary block", type: "button", onclick: () => actions.add(selected) },
          "+ Add exercise"),
        nothingAnywhere && !day ? null : dayHeadCard(day, exercises),
        day && exercises.length ? statTiles(day) : null,
        exercises.length ? summaryCard(day) : null,
        exercises.length ? musclesCard(day) : null),
      h("div", { class: "col" },
        nothingAnywhere && !day ? exampleDay() : exerciseList(day, exercises),
        twoWaysCard()));
  }

  // ------------------------------------------------------------- calendar

  function calendarCard() {
    const { year, month: m } = month;
    const step = (delta) => {
      // The arrows change the month without changing the selection.
      const next = new Date(Date.UTC(year, m - 1 + delta, 1));
      month = { year: next.getUTCFullYear(), month: next.getUTCMonth() + 1 };
      render();
    };
    const log = byDate();
    const inMonth = days.filter((d) => d.date.startsWith(`${year}-${pad(m)}`) && L.exercisesInOrder(d).length);
    const gym = inMonth.filter((d) => L.placeOf(d).place === "gym").length;
    const home = inMonth.length - gym;
    const count = (n, word) => `${n} ${word} day${n === 1 ? "" : "s"}`;

    return h("div", { class: "card cal" },
      h("div", { class: "cal-top" },
        h("button", { class: "cal-arrow", type: "button", "aria-label": "Previous month", onclick: () => step(-1) }, "‹"),
        h("h2", { class: "cal-month" }, L.fmtMonth(iso(year, m, 1))),
        h("button", {
          class: "btn cal-today", type: "button",
          onclick: () => { month = { year: Number(today.slice(0, 4)), month: Number(today.slice(5, 7)) }; setSelected(today); },
        }, "Today"),
        h("button", { class: "cal-arrow", type: "button", "aria-label": "Next month", onclick: () => step(1) }, "›")),
      h("div", { class: "cal-head", "aria-hidden": "true" },
        WEEKDAY_INITIALS.map((letter) => h("span", {}, letter))),
      h("div", { class: "cal-grid", role: "grid", "aria-label": "Calendar" },
        monthGrid(year, m).map((week) => h("div", { class: "cal-week", role: "row" },
          week.map((date, i) => dayCell(date, log.get(date), i))))),
      h("p", { class: "cal-legend" },
        h("span", { class: "key" }, h("span", { class: "key-dot gym", "aria-hidden": "true" }), "Gym"),
        h("span", { class: "key" }, h("span", { class: "key-dot home", "aria-hidden": "true" }), "Home"),
        h("span", { class: "meta" }, inMonth.length
          ? [gym ? count(gym, "gym") : null, home ? count(home, "home") : null].filter(Boolean).join(L.MIDDOT) + " this month"
          : "No workouts this month")));
  }

  function dayCell(date, day, index) {
    if (!date) return h("span", { class: "cal-cell", role: "gridcell", "aria-hidden": "true" });
    const logged = day ? L.exercisesInOrder(day).length : 0;
    const place = logged ? L.placeOf(day).place : null;
    const label = [
      L.fmtDateLong(date, today),
      logged ? `${place === "gym" ? "Gym" : "Home"} workout, ${logged} ${logged === 1 ? "exercise" : "exercises"}`
        : "nothing logged",
    ].join(": ");
    return h("div", { class: "cal-cell", role: "gridcell" },
      h("button", {
        type: "button",
        class: "cal-day",
        dataset: { today: String(date === today), selected: String(date === selected), future: String(date > today) },
        "aria-label": label,
        "aria-pressed": String(date === selected),
        onclick: () => setSelected(date),
      }, h("span", { class: "cal-num" }, Number(date.slice(8))),
      // Always present, so every circle in the grid sits at the same height.
      h("span", { class: place ? `cal-dot ${place}` : "cal-dot blank", "aria-hidden": "true" })));
  }

  // ------------------------------------------------------------- day head

  function dayHeadCard(day, exercises) {
    if (!selected) {
      return h("div", { class: "card" }, h("p", { class: "meta" }, "Pick a day on the calendar."));
    }
    const meta = [
      exercises.length ? `${exercises.length} ${exercises.length === 1 ? "exercise" : "exercises"}` : null,
      loggedRange(exercises),
      day && typeof day.bodyweight === "number" ? `Bodyweight ${L.fmtNum(day.bodyweight)} lb` : null,
    ].filter(Boolean);

    return h("div", { class: "card" },
      h("h2", { class: "section-title day-title" },
        L.fmtDateLong(selected, today), selected === today ? h("span", { class: "today-tag" }, L.MIDDOT, "Today") : null),
      meta.length ? h("p", { class: "meta" }, meta.join(L.MIDDOT)) : null,
      exercises.length ? whereControl(day) : null);
  }

  function whereControl(day) {
    const { place, guessed } = L.placeOf(day);
    const pick = (next) => patch({ place: next }, `Marked ${L.fmtDateShort(selected, today)} as a ${next} day.`);
    return h("div", { class: "where" },
      h("p", { class: "label" }, "Where"),
      h("div", { class: "seg where-seg", role: "group", "aria-label": "Where this workout happened" },
        ["gym", "home"].map((option) => h("button", {
          type: "button",
          "aria-selected": String(place === option && !guessed),
          onclick: () => pick(option),
        }, h("span", { class: `key-dot ${option}`, "aria-hidden": "true" }),
        option === "gym" ? "Gym" : "Home"))),
      guessed ? h("p", { class: "meta" }, "Guessed from the exercises. Tap to set it.") : null);
  }

  // ----------------------------------------------------------- stat tiles

  function statTiles(day) {
    const totals = L.dayTotals(day);
    const reps = totals.reps.hasRange
      ? `${L.fmtNum(totals.reps.low)}${L.EN_DASH}${L.fmtNum(totals.reps.high)}`
      : L.fmtNum(totals.reps.low);
    const tile = (name, value, wide = false) => h("div", { class: "card tile" },
      h("p", { class: `stat-value${wide ? " ranged" : ""}` }, value),
      h("p", { class: "label" }, name));
    return h("div", { class: "tiles" },
      tile("Exercises", L.fmtNum(totals.exercises)),
      tile("Sets", L.fmtNum(totals.sets)),
      tile("Reps", reps, totals.reps.hasRange),
      tile("Cardio", totals.cardioMinutes ? `${L.fmtNum(totals.cardioMinutes)} min` : "—",
        Boolean(totals.cardioMinutes)));
  }

  // --------------------------------------------------------- summary card

  // One editable field with its own Save button, enabled only once the text
  // differs from what the server holds. Typing touches just the counter and
  // the button: rebuilding a text box mid-sentence would cost the caret and
  // the undo history.
  function editable({ field, id, label, saved, build, toValue, done, limit }) {
    const value = drafts[field] !== undefined ? drafts[field] : saved;
    const input = build({ id, value });
    const counter = limit ? h("span", { class: "meta count" }, `${value.length}/${limit}`) : h("span");
    const button = h("button", {
      class: "btn", type: "button", disabled: value === saved,
      onclick: () => {
        const next = toValue(input.value);
        // toValue hands back a message instead when the text isn't usable.
        if (typeof next === "string") return toast(next);
        patch({ [field]: next }, done, field);
      },
    }, "Save");
    input.addEventListener("input", () => {
      drafts[field] = input.value;
      if (limit) counter.textContent = `${input.value.length}/${limit}`;
      button.disabled = input.value === saved;
    });
    return h("div", { class: "edit" },
      label ? h("label", { class: "label", for: id }, label) : null,
      input,
      h("div", { class: "edit-foot" }, counter, button));
  }

  function summaryCard(day) {
    const text = (value) => (String(value).trim() ? String(value).trim() : null);
    return h("div", { class: "card" },
      h("p", { class: "label" }, "Day summary"),
      editable({
        field: "summary",
        id: "day-summary",
        saved: day.summary || "",
        toValue: text,
        done: "Summary saved.",
        limit: MAX_SUMMARY,
        // A textarea's text is its content, not a value attribute.
        build: ({ id, value }) => h("textarea", {
          id, class: "box", rows: 4, maxlength: MAX_SUMMARY,
          "aria-label": "Day summary",
          placeholder: "No summary yet. Tap Done for today, or write your own.",
        }, value),
      }),
      editable({
        field: "notes",
        id: "day-notes",
        label: "Notes",
        saved: day.notes || "",
        toValue: text,
        done: "Notes saved.",
        build: ({ id, value }) => h("textarea", {
          id, class: "box", rows: 2, maxlength: 1000,
          placeholder: "How it felt, especially your back.",
        }, value),
      }),
      editable({
        field: "bodyweight",
        id: "day-bodyweight",
        label: "Bodyweight (lb)",
        saved: typeof day.bodyweight === "number" ? String(day.bodyweight) : "",
        // Blank removes it; junk is refused rather than quietly clearing it.
        toValue: (value) => {
          if (!String(value).trim()) return null;
          const weight = parseNumber(value, { min: 30, max: 1000 });
          return weight.bad ? "Bodyweight should be a number like 180." : weight.value;
        },
        done: "Bodyweight saved.",
        build: ({ id, value }) => h("input", {
          id, class: "box short", type: "text", inputmode: "decimal", placeholder: "\u2014", value,
        }),
      }));
  }

  // --------------------------------------------------------- sets per muscle

  function musclesCard(day) {
    const rows = L.setsPerMuscle([day]);
    const cardio = L.cardioTotals([day]);
    const top = rows.length ? rows[0].sets : 0;
    return h("div", { class: "card" },
      h("p", { class: "label" }, "Sets per muscle"),
      rows.length ? h("div", { class: "bars" }, rows.map((row) => h("div", { class: "bar-row" },
        h("span", { class: "bar-name" }, row.muscle),
        // Never invisible: the smallest count still shows a sliver.
        h("span", { class: "bar-track" },
          h("span", { class: "bar-fill", style: { width: `${Math.max(6, Math.round((row.sets / top) * 100))}%` } })),
        h("span", { class: "bar-count" }, `${row.sets} ${row.sets === 1 ? "set" : "sets"}`))))
        : h("p", { class: "meta" }, "No strength sets on this day."),
      cardio.minutes
        ? h("p", { class: "meta" }, `Cardio: ${L.fmtNum(cardio.minutes)} min`
          + (cardio.distance ? `, ${L.fmtDistance(cardio.distance)}` : ""))
        : null);
  }

  // ------------------------------------------------------ exercise cards

  function exerciseList(day, exercises) {
    if (!selected) return h("div", { class: "card" }, h("p", { class: "meta" }, "Pick a day on the calendar."));
    if (!exercises.length) {
      return h("div", { class: "empty-day" },
        h("p", {}, "Nothing logged for this day. Use + Add exercise to fill it in, or tap the mic."));
    }
    return h("div", { class: "ex-list" },
      entriesOf(day).map(([key, ex], i) => exerciseCard(ex, i + 1, key)));
  }

  const COLUMNS = [
    { key: "reps", head: "Reps", cell: (s) => L.fmtReps(s.reps ?? null, s.repsMax ?? null) },
    { key: "weight", head: "Weight", cell: (s) => L.fmtNum(s.weight) },
    { key: "seconds", head: "Hold", cell: (s) => L.fmtHold(s.seconds) },
    { key: "minutes", head: "Time", cell: (s) => `${L.fmtNum(s.minutes)} min` },
    { key: "distance", head: "Distance", cell: (s) => L.fmtDistance(s.distance, s.distanceUnit) },
    { key: "note", head: "Note", cell: (s) => s.note },
  ];

  function exerciseCard(ex, number, key) {
    const area = L.groupOf(ex);
    const muscles = L.musclesOf(ex);
    const sets = ex.sets || [];
    // Only the columns some set actually uses.
    const columns = COLUMNS.filter((c) => sets.some((s) => s[c.key] !== undefined && s[c.key] !== null));
    const removing = pendingRemove === key;

    return h("div", { class: "card ex" },
      h("div", { class: "ex-top" },
        h("span", { class: "ex-num" }, number),
        h("button", {
          class: "ex-name", type: "button",
          onclick: () => actions.openProgress(ex.exercise),
        }, ex.exercise)),
      h("div", { class: "chips" },
        h("span", { class: "chip" },
          h("span", { class: "ring", dataset: { area: area.toLowerCase() }, "aria-hidden": "true" }),
          area),
        muscles.map((muscle, i) => h("span", { class: `pill muscle${i === 0 ? " main" : ""}` }, muscle))),
      h("p", { class: "compact" }, L.compactLine(ex)),
      columns.length ? h("div", { class: "table-wrap" },
        h("table", { class: "sets" },
          h("thead", {}, h("tr", {},
            h("th", { scope: "col" }, "Set"),
            columns.map((c) => h("th", { scope: "col" },
              c.key === "weight" ? `Weight (${ex.unit || "lb"}${ex.perHand ? ", each" : ""})` : c.head)))),
          h("tbody", {}, sets.map((s, i) => h("tr", {},
            h("th", { scope: "row" }, i + 1),
            columns.map((c) => h("td", {}, s[c.key] === undefined || s[c.key] === null ? "—" : c.cell(s)))))))) : null,
      ex.notes ? h("p", { class: "meta ex-note" }, `Note: ${ex.notes}`) : null,
      removing
        ? h("div", { class: "ex-actions" },
          h("span", { class: "meta" }, "Remove this exercise?"),
          h("button", {
            class: "btn danger", type: "button",
            onclick: () => { pendingRemove = null; save(() => actions.api.removeExercise(selected, key), { done: `Removed ${ex.exercise}.` }); },
          }, "Remove"),
          h("button", { class: "btn", type: "button", onclick: () => { pendingRemove = null; render(); } }, "Cancel"))
        : h("div", { class: "ex-actions" },
          h("button", { class: "btn", type: "button", onclick: () => actions.edit(selected, key, ex) }, "Edit"),
          h("button", {
            class: "btn danger", type: "button",
            onclick: () => { pendingRemove = key; render(); },
          }, "Remove")));
  }

  // ---------------------------------------------------------- example day

  // Shown only when there is no data at all: something to look at that makes
  // the shape of a day obvious.
  const EXAMPLE = [
    { exercise: "Glute bridge", group: "Legs", muscles: ["Glutes", "Hamstrings"], unit: "lb",
      sets: [{ reps: 12 }, { reps: 12 }, { reps: 12 }] },
    { exercise: "Bird dog", group: "Core", muscles: ["Core", "Lower back", "Glutes"], unit: "lb",
      sets: [{ seconds: 10, note: "each side" }, { seconds: 10, note: "each side" }, { seconds: 10, note: "each side" }] },
    { exercise: "Dumbbell bench press", group: "Chest", muscles: ["Chest", "Triceps", "Front shoulders"],
      unit: "lb", perHand: true, notes: "Last set felt heavy.",
      sets: [{ reps: 10, weight: 25 }, { reps: 10, weight: 25 }, { reps: 8, weight: 30 }] },
    { exercise: "Lat pulldown", group: "Back", muscles: ["Lats", "Biceps", "Mid back"], unit: "lb",
      sets: [{ reps: 10, weight: 70 }, { reps: 10, weight: 70 }, { reps: 10, weight: 70 }] },
  ];

  function exampleDay() {
    return h("div", { class: "example" },
      h("p", { class: "notice banner" },
        h("strong", {}, "Example day."),
        " Nothing is logged yet. Your own workouts replace this after the first exercise."),
      h("div", { class: "card dashed" },
        h("p", { class: "label" }, "Day summary"),
        h("p", {}, "Example summary: legs, chest and back. 4 exercises, 12 sets.")),
      EXAMPLE.map((ex, i) => h("div", { class: "card ex dashed" },
        h("div", { class: "ex-top" },
          h("span", { class: "ex-num" }, i + 1),
          h("span", { class: "ex-name plain" }, ex.exercise)),
        h("div", { class: "chips" },
          h("span", { class: "chip" },
            h("span", { class: "ring", dataset: { area: L.groupOf(ex).toLowerCase() }, "aria-hidden": "true" }),
            L.groupOf(ex)),
          L.musclesOf(ex).map((muscle, j) => h("span", { class: `pill muscle${j === 0 ? " main" : ""}` }, muscle))),
        h("p", { class: "compact" }, L.compactLine(ex)),
        ex.notes ? h("p", { class: "meta ex-note" }, `Note: ${ex.notes}`) : null)));
  }

  // -------------------------------------------------------- two ways to log

  function twoWaysCard() {
    const line = (...parts) => h("li", {}, ...parts);
    return h("div", { class: "card ways" },
      h("p", { class: "label" }, "Two ways to log"),
      h("ul", { class: "ways-list" },
        line("Tap the mic after each exercise, or use ", h("strong", {}, "+ Add exercise"), "."),
        line(h("strong", {}, "Edit"), " or ", h("strong", {}, "Remove"), " fixes an exercise."),
        line("To fill in a past day, tap it on the calendar first."),
        line("When you finish, tap ", h("strong", {}, "Done for today"), "."),
        line(h("span", { class: "key-dot gym", "aria-hidden": "true" }), " Dark green dot = gym day, ",
          h("span", { class: "key-dot home", "aria-hidden": "true" }), " yellow dot = home day. Say ",
          h("em", {}, "at the gym"), " or ", h("em", {}, "at home"), ", or use the Where switch. ",
          "Days without either are guessed: weighted exercises mean gym, otherwise home.")),
      h("p", { class: "label" }, "Example phrases"),
      h("ul", { class: "ways-list quotes" },
        line(h("em", {}, "“Seated row, 3 sets of 10 to 12 at 40 pounds.”")),
        line(h("em", {}, "“Treadmill walk, 25 minutes, 1.3 miles.”")),
        line(h("em", {}, "“Bird dogs, 3 rounds of 10-second holds each side.”")),
        line(h("em", {}, "“Done for today.”"))),
      h("p", { class: "meta" },
        "Muscles are the 2–3 that each exercise works most, main one first. In Sets per muscle, ",
        "each set counts toward every muscle listed for that exercise; cardio counts in minutes instead."));
  }

  return { update, select: setSelected, selected: () => selected };
}
