// The Trends tab: how much work went in over a period, which muscles got it,
// and which main areas got none.
//
// Everything is computed from the log already in memory, so changing the range
// costs nothing and works offline. The range itself is remembered through
// store.js, the only file allowed to touch browser storage.

import { h, replace } from "../dom.js";
import * as L from "../logic.js";

const CELL = 12;
const GAP = 3;
const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const RANGES = [
  { key: "7", label: "7 days", days: 7, heading: "Last 7 days" },
  { key: "30", label: "30 days", days: 30, heading: "Last 30 days" },
  { key: "90", label: "90 days", days: 90, heading: "Last 90 days" },
  { key: "365", label: "Year", days: 365, heading: "Last 12 months" },
  { key: "all", label: "All", days: null, heading: "All time" },
];

const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
const shift = (date, days) => {
  const d = L.parseDate(date);
  d.setUTCDate(d.getUTCDate() + days);
  return iso(d);
};
const daysBetween = (from, to) => Math.round((L.parseDate(to) - L.parseDate(from)) / 86_400_000) + 1;

// The window a range covers, inclusive at both ends. "All" starts at the first
// logged day, so a brand-new log doesn't claim to span a year.
export function windowFor(rangeKey, days, today) {
  const range = RANGES.find((r) => r.key === rangeKey) || RANGES[1];
  if (range.days) return { from: shift(today, -(range.days - 1)), to: today, range };
  const logged = days.filter((d) => L.exercisesInOrder(d).length).map((d) => d.date).sort();
  return { from: logged.length ? logged[0] : today, to: today, range };
}

const inWindow = (days, from, to) => days.filter((d) => d.date >= from && d.date <= to && L.exercisesInOrder(d).length);

// One column per week, Sunday at the top, oldest week first.
export function heatWeeks(from, to) {
  const start = L.parseDate(from);
  start.setUTCDate(start.getUTCDate() - start.getUTCDay()); // back to Sunday
  const weeks = [];
  for (let cursor = iso(start); cursor <= to; cursor = shift(cursor, 7)) {
    weeks.push(Array.from({ length: 7 }, (_, i) => {
      const date = shift(cursor, i);
      return date >= from && date <= to ? date : null;
    }));
  }
  return weeks;
}

export function createTrendsTab({ root, store, actions, today }) {
  let days = [];
  let rangeKey = String(store.pref("trendsRange", "30"));
  if (!RANGES.some((r) => r.key === rangeKey)) rangeKey = "30";

  function update(nextDays, nextToday) {
    days = nextDays || [];
    if (nextToday) today = nextToday;
    render();
  }

  function pickRange(key) {
    rangeKey = key;
    store.setPref("trendsRange", key);
    render();
  }

  function render() {
    replace(root, body());
  }

  function body() {
    const { from, to, range } = windowFor(rangeKey, days, today);
    const span = inWindow(days, from, to);
    const length = daysBetween(from, to);

    return h("div", { class: "stack" },
      h("div", { class: "seg range", role: "group", "aria-label": "Range" },
        RANGES.map((r) => h("button", {
          type: "button", "aria-selected": String(r.key === rangeKey), onclick: () => pickRange(r.key),
        }, r.label))),
      h("div", { class: "card" },
        h("h2", { class: "section-title" }, range.heading),
        h("p", { class: "meta" }, `${L.fmtDateShort(from, today)} ${L.EN_DASH} ${L.fmtDateShort(to, today)}`)),
      // Two different empty states: nothing ever, or nothing in this window.
      !days.some((d) => L.exercisesInOrder(d).length)
        ? h("div", { class: "card" }, h("p", { class: "meta" }, "Trends appear once you log workouts."))
        : !span.length
          ? h("div", { class: "card" }, h("p", { class: "meta" }, "Nothing logged in this period."))
          : [tiles(span, length), placeLine(span), heatmap(from, to, range),
            musclesCard(span), missingCard(span), distanceLine(span)]);
  }

  function tiles(span, length) {
    const strengthSets = span.reduce((n, day) => n + L.dayTotals(day).strengthSets, 0);
    const cardio = L.cardioTotals(span);
    // Under a week there is no meaningful per-week figure to give.
    const perWeek = length >= 7 ? L.fmtNum(Math.round((span.length / (length / 7)) * 10) / 10) : "—";
    const tile = (name, value) => h("div", { class: "card tile" },
      h("p", { class: "stat-value" }, value), h("p", { class: "label" }, name));
    return h("div", { class: "tiles" },
      tile("Workouts", L.fmtNum(span.length)),
      tile("Per week", perWeek),
      tile("Strength sets", L.fmtNum(strengthSets)),
      tile("Cardio", cardio.minutes ? `${L.fmtNum(cardio.minutes)} min` : "—"));
  }

  function placeLine(span) {
    // A guessed place still counts: every logged day is one or the other.
    const gym = span.filter((d) => L.placeOf(d).place === "gym").length;
    const home = span.length - gym;
    const part = (n, word, cls) => h("span", { class: "key" },
      h("span", { class: `key-dot ${cls}`, "aria-hidden": "true" }),
      `${n} ${word} day${n === 1 ? "" : "s"}`);
    return h("p", { class: "cal-legend" },
      gym ? part(gym, "gym", "gym") : null,
      home ? part(home, "home", "home") : null);
  }

  function heatmap(from, to, range) {
    // Under a week there is nothing for it to show.
    if (range.days === 7) return null;
    const load = new Map(days.map((d) => [d.date, L.dayLoad(d)]));
    const weeks = heatWeeks(from, to);
    const scroller = h("div", { class: "heat-scroll" },
      h("div", { class: "heat" },
        h("div", { class: "heat-months", "aria-hidden": "true" }, monthLabels(weeks)),
        h("div", { class: "heat-grid" }, weeks.map((week) => h("div", { class: "heat-week" },
          week.map((date) => cell(date, load.get(date) || 0)))))));
    // Today is at the right-hand end, which is where the interesting part is.
    queueMicrotask(() => { scroller.scrollLeft = scroller.scrollWidth; });
    return h("div", { class: "card" },
      h("p", { class: "label" }, "Workout days"),
      scroller,
      h("p", { class: "heat-key" }, "Less",
        [0, 1, 2, 3].map((level) => h("span", { class: "heat-cell", dataset: { level }, "aria-hidden": "true" })),
        "More sets"));
  }

  function cell(date, sets) {
    if (!date) return h("span", { class: "heat-cell empty", "aria-hidden": "true" });
    return h("button", {
      type: "button",
      class: "heat-cell",
      dataset: { level: L.heatLevel(sets), today: String(date === today) },
      "aria-label": `${L.fmtDateShort(date, today)}: ${sets} ${sets === 1 ? "set" : "sets"}`,
      onclick: () => actions.openDay(date),
    });
  }

  // A label above the first column of each month, positioned by column index.
  function monthLabels(weeks) {
    const out = [];
    let last = null;
    weeks.forEach((week, index) => {
      const first = week.find(Boolean);
      if (!first) return;
      const month = first.slice(0, 7);
      if (month === last) return;
      last = month;
      out.push(h("span", {
        class: "heat-month",
        style: { left: `${index * (CELL + GAP)}px` },
      }, MONTHS_SHORT[Number(first.slice(5, 7)) - 1]));
    });
    return out;
  }

  function musclesCard(span) {
    const rows = L.setsPerMuscle(span);
    if (!rows.length) return null;
    const top = rows[0].sets;
    return h("div", { class: "card" },
      h("p", { class: "label" }, "Sets per muscle"),
      h("div", { class: "bars" }, rows.map((row) => h("div", { class: "bar-row" },
        h("span", { class: "bar-name" }, row.muscle),
        h("span", { class: "bar-track" },
          h("span", { class: "bar-fill", style: { width: `${Math.max(6, Math.round((row.sets / top) * 100))}%` } })),
        h("span", { class: "bar-count" }, `${row.sets} ${row.sets === 1 ? "set" : "sets"}`)))));
  }

  function missingCard(span) {
    const missing = L.missingAreas(span);
    return h("div", { class: "card" },
      h("p", { class: "label" }, "Not worked in this period"),
      missing.length
        ? h("div", { class: "chips" }, missing.map((area) => h("span", { class: "pill missing" }, area)))
        : h("p", { class: "meta" }, "Every main area got at least one set."));
  }

  function distanceLine(span) {
    const { distance } = L.cardioTotals(span);
    if (!distance) return null;
    return h("p", { class: "meta" }, `Cardio distance in this period: ${L.fmtDistance(distance)}.`);
  }

  return { update, range: () => rangeKey };
}
