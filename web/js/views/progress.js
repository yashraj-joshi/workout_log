// The Progress tab: one exercise at a time, its top set over time, and the
// session history.
//
// The chart is inline SVG drawn at the container's real width - no chart
// library, the same way the page this replaces did it. A ResizeObserver
// redraws it when the width changes, which also covers arriving here from
// another tab, where the panel was hidden and measured zero.

import { h, replace } from "../dom.js";
import * as L from "../logic.js";

const PAD = { top: 14, right: 14, bottom: 26, left: 44 };
const HEIGHT = 200;
const LATEST = 15;
const SVG = "http://www.w3.org/2000/svg";

// h() makes HTML elements; SVG needs its own namespace or nothing renders.
function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    el.setAttribute(name, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

// About four ticks on a round number, so the axis reads 0, 20, 40, 60 rather
// than 0, 17.5, 35, 52.5.
export function ticksFor(min, max, count = 4) {
  const low = Math.min(min, max);
  const high = Math.max(min, max);
  if (low === high) return [low];
  const raw = (high - low) / (count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((candidate) => candidate >= raw) || magnitude * 10;
  // Round outwards at both ends, so the ticks always contain the data: with
  // 0 to 12 and a step of 5 the top tick has to be 15, not 10.
  const start = Math.floor(low / step) * step;
  const end = Math.ceil(high / step) * step;
  const count_ = Math.round((end - start) / step);
  // Stepped by index rather than by repeated addition, which drifts on
  // fractional steps like 0.5.
  return Array.from({ length: count_ + 1 }, (_, i) => Math.round((start + i * step) * 100) / 100);
}

// Every exercise I have logged, most recently done first.
export function exerciseList(days) {
  const seen = new Map();
  for (const day of days) {
    for (const ex of L.exercisesInOrder(day)) {
      const key = L.normalizeWords(ex.exercise || "").join(" ");
      if (!key) continue;
      const row = seen.get(key) || { name: ex.exercise, sessions: 0, last: "" };
      row.sessions += 1;
      if ((day.date || "") > row.last) {
        row.last = day.date || "";
        row.name = ex.exercise; // the most recent spelling wins
      }
      seen.set(key, row);
    }
  }
  // Most recent first. Two exercises done on the same day are ordered by name
  // so the picker doesn't reshuffle itself every time the log reloads.
  return [...seen.values()].sort((a, b) => {
    if (a.last !== b.last) return a.last > b.last ? -1 : 1;
    return a.name.toLowerCase() < b.name.toLowerCase() ? -1 : a.name.toLowerCase() > b.name.toLowerCase() ? 1 : 0;
  });
}

export function createProgressTab({ root, store, actions, today }) {
  let days = [];
  let chosen = store.pref("progressExercise", null);
  let showAll = false;
  let observer = null;

  function update(nextDays, nextToday) {
    days = nextDays || [];
    if (nextToday) today = nextToday;
    render();
  }

  // Called when a name is tapped on the Day tab.
  function select(name) {
    chosen = name;
    showAll = false;
    store.setPref("progressExercise", name);
    render();
  }

  function render() {
    if (observer) observer.disconnect();
    observer = null;
    replace(root, body());
  }

  function body() {
    const list = exerciseList(days);
    if (!list.length) {
      return h("div", { class: "card" }, h("p", { class: "meta" },
        "Progress appears once you log an exercise. Log the same exercise on different days to see the trend."));
    }
    // A remembered choice that is no longer in the log falls back to the
    // most recent exercise.
    const current = list.find((row) => L.sameExercise(row.name, chosen || "")) || list[0];
    const sessions = L.exerciseSessions(days, current.name);
    const kind = L.dominantKind(sessions);
    const plotted = sessions.filter((row) => row.topSet && row.topSet.kind === kind);

    return h("div", { class: "stack" },
      picker(list, current),
      header(current, sessions, plotted),
      plotted.length >= 2
        ? chartCard(plotted)
        : h("div", { class: "card" }, h("p", { class: "meta" },
          "A chart of your top set appears after the second session of this exercise.")),
      historyCard(sessions));
  }

  function picker(list, current) {
    const select_ = h("select", { class: "box", "aria-label": "Exercise" },
      list.map((row) => h("option", {
        value: row.name,
        selected: row === current || undefined,
      }, `${row.name} (${row.sessions})`)));
    select_.addEventListener("change", () => select(select_.value));
    return h("div", { class: "field" }, h("label", { class: "label" }, "Exercise"), select_);
  }

  function header(current, sessions, plotted) {
    const last = sessions[sessions.length - 1];
    const best = plotted.reduce((a, b) => (b.topSet.value > a.topSet.value ? b : a), plotted[0] || last);
    const muscles = last ? L.musclesOf(last.exercise) : [];
    const tile = (name, value) => h("div", { class: "card tile" },
      h("p", { class: "stat-value ranged" }, value), h("p", { class: "label" }, name));
    return h("div", { class: "stack" },
      h("div", { class: "card" },
        h("h2", { class: "section-title" }, current.name),
        h("div", { class: "chips" },
          muscles.map((muscle, i) => h("span", { class: `pill muscle${i === 0 ? " main" : ""}` }, muscle)))),
      h("div", { class: "tiles three" },
        tile("Best", best && best.topSet ? best.topSet.label : "—"),
        tile("Sessions", String(sessions.length)),
        tile("Last done", last ? L.fmtDateShort(last.date, today) : "—")));
  }

  // ------------------------------------------------------------------ chart

  function chartCard(plotted) {
    const holder = h("div", { class: "chart" });
    const tip = h("p", { class: "meta chart-tip" }, " ");
    const draw = (width) => replace(holder, chart(plotted, Math.max(260, width), tip));
    // The panel can be hidden (width 0) when this is built; the observer fires
    // with the real width as soon as it is shown.
    observer = new ResizeObserver((entries) => {
      const width = Math.round(entries[0].contentRect.width);
      if (width > 0 && width !== holder.dataset.width) {
        holder.dataset.width = String(width);
        draw(width);
      }
    });
    const card = h("div", { class: "card" },
      h("p", { class: "label" }, "Top set over time"), holder, tip);
    queueMicrotask(() => observer && observer.observe(card));
    return card;
  }

  function chart(plotted, width, tip) {
    const values = plotted.map((row) => row.topSet.value);
    const ticks = ticksFor(Math.min(...values), Math.max(...values));
    const lo = Math.min(...ticks, ...values);
    const hi = Math.max(...ticks, ...values);
    const plotW = width - PAD.left - PAD.right;
    const plotH = HEIGHT - PAD.top - PAD.bottom;
    const x = (i) => PAD.left + (plotted.length === 1 ? plotW / 2 : (i / (plotted.length - 1)) * plotW);
    const y = (value) => PAD.top + (hi === lo ? plotH / 2 : (1 - (value - lo) / (hi - lo)) * plotH);
    const unit = { weight: plotted[0].topSet.unit || "lb", seconds: "sec", minutes: "min", reps: "reps" }[plotted[0].topSet.kind];
    // Only worth marking when there is something to stand out from: on a flat
    // line every point is the best, and hollowing all of them says nothing.
    const bestValue = Math.max(...values) > Math.min(...values) ? Math.max(...values) : null;

    const rule = s("line", { class: "chart-rule", x1: 0, x2: 0, y1: PAD.top, y2: PAD.top + plotH, opacity: 0 });
    const svg = s("svg", {
      class: "chart-svg", width, height: HEIGHT, viewBox: `0 0 ${width} ${HEIGHT}`,
      role: "img", "aria-label": `Top set over time, ${plotted.length} sessions, in ${unit}`,
    },
    // Gridlines and their labels.
    ticks.map((tick) => [
      s("line", { class: "chart-grid", x1: PAD.left, x2: width - PAD.right, y1: y(tick), y2: y(tick) }),
      s("text", { class: "chart-label", x: PAD.left - 6, y: y(tick) + 4, "text-anchor": "end" }, L.fmtNum(tick)),
    ]),
    s("text", { class: "chart-label", x: PAD.left - 6, y: PAD.top - 4, "text-anchor": "end" }, unit),
    rule,
    s("polyline", {
      class: "chart-line", fill: "none",
      points: plotted.map((row, i) => `${x(i)},${y(row.topSet.value)}`).join(" "),
    }),
    // The best session is hollow, so it stands out without a second colour.
    plotted.map((row, i) => s("circle", {
      class: bestValue !== null && row.topSet.value === bestValue ? "chart-dot best" : "chart-dot",
      cx: x(i), cy: y(row.topSet.value), r: 3.5,
    })),
    // First, last, and the middle one once there is room for it.
    [0, plotted.length > 4 ? Math.floor((plotted.length - 1) / 2) : null, plotted.length - 1]
      .filter((i) => i !== null)
      .map((i) => s("text", {
        class: "chart-label", x: x(i), y: HEIGHT - 8,
        "text-anchor": i === 0 ? "start" : i === plotted.length - 1 ? "end" : "middle",
      }, L.fmtDateShort(plotted[i].date, today))));

    // Scrub: move or drag anywhere over the chart to read a session off it.
    const nearest = (event) => {
      const box = svg.getBoundingClientRect();
      const at = event.clientX - box.left;
      let best = 0;
      plotted.forEach((_, i) => { if (Math.abs(x(i) - at) < Math.abs(x(best) - at)) best = i; });
      return best;
    };
    const show = (event) => {
      const i = nearest(event);
      rule.setAttribute("x1", x(i));
      rule.setAttribute("x2", x(i));
      rule.setAttribute("opacity", 1);
      tip.textContent = `${L.fmtDateShort(plotted[i].date, today)}${L.MIDDOT}${plotted[i].topSet.label}`;
    };
    svg.addEventListener("pointermove", show);
    svg.addEventListener("pointerdown", show);
    svg.addEventListener("pointerleave", () => {
      rule.setAttribute("opacity", 0);
      tip.textContent = " ";
    });
    return svg;
  }

  // ---------------------------------------------------------------- history

  function historyCard(sessions) {
    const newest = [...sessions].reverse();
    const rows = showAll ? newest : newest.slice(0, LATEST);
    return h("div", { class: "card" },
      h("p", { class: "label" }, "History"),
      h("div", { class: "table-wrap" },
        h("table", { class: "sets history" },
          h("thead", {}, h("tr", {},
            ["Date", "Sets", "Top set", "Change"].map((name) => h("th", { scope: "col" }, name)))),
          h("tbody", {}, rows.map((row) => {
            // The index must be the one in the oldest-first list, because
            // "previous" means the session before this one.
            const index = sessions.indexOf(row);
            const change = L.changeLabel(row.topSet, (L.previousSession(sessions, index) || {}).topSet || null);
            return h("tr", {},
              h("th", { scope: "row" },
                h("button", { class: "linklike cell", type: "button", onclick: () => actions.openDay(row.date) },
                  L.fmtDateShort(row.date, today))),
              h("td", {}, L.compactLine(row.exercise)),
              h("td", {}, row.topSet ? row.topSet.label : "—"),
              h("td", { class: `change ${changeTone(change)}` }, change));
          })))),
      newest.length > LATEST
        ? h("button", {
          class: "linklike", type: "button",
          onclick: () => { showAll = !showAll; render(); },
        }, showAll ? `Show latest ${LATEST}` : `Show all ${newest.length} sessions`)
        : null);
  }

  return { update, select, chosen: () => chosen };
}

// "+5 lb" reads good, "−2 reps" reads bad, "same" and "first" are just facts.
export function changeTone(change) {
  if (change.startsWith("+")) return "good";
  if (change.startsWith(L.MINUS)) return "bad";
  return "faint";
}
