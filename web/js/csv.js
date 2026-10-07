// Export CSV, one row per set.
//
// The twin of backend/src/workoutlog/export_csv.py, checked against the same
// golden file (shared/fixtures/export_expected.csv) in both languages. The
// browser builds the file from the log it already has, so exporting costs no
// API call and works offline.

import * as L from "./logic.js";

export const HEADER = [
  "Date", "Order", "Exercise", "Area", "Muscles", "Set",
  "Reps", "Reps max", "Weight", "Unit", "Per dumbbell",
  "Hold (s)", "Time (min)", "Distance", "Distance unit",
  "Set note", "Exercise note", "Logged at",
];

// Raw values, not display-rounded: the file is for analysis, not reading.
function num(value) {
  if (value === null || value === undefined) return "";
  const number = Number(value);
  return Number.isInteger(number) ? String(number) : String(number);
}

export function rowsFor(days) {
  const out = [];
  const ordered = [...days].sort((a, b) => ((a.date || "") < (b.date || "") ? -1 : (a.date || "") > (b.date || "") ? 1 : 0));
  for (const day of ordered) {
    const date = day.date || "";
    for (const ex of L.exercisesInOrder(day)) {
      const unit = ex.unit || "lb";
      const muscles = L.musclesOf(ex).join("; ");
      (ex.sets || []).forEach((s, i) => {
        const index = i + 1;
        const hasWeight = s.weight !== null && s.weight !== undefined;
        const hasDistance = s.distance !== null && s.distance !== undefined;
        const first = index === 1;
        out.push([
          date,
          String(ex.order || ""),
          ex.exercise || "",
          L.groupOf(ex),
          muscles,
          String(index),
          num(s.reps),
          num(s.repsMax),
          num(s.weight),
          hasWeight ? unit : "",
          ex.perHand ? "yes" : "",
          num(s.seconds),
          num(s.minutes),
          num(s.distance),
          hasDistance ? (s.distanceUnit || "mi") : "",
          s.note || "",
          // Only on the exercise's first row, so a spreadsheet does not repeat
          // the same note down every set.
          first ? (ex.notes || "") : "",
          first ? (ex.loggedAt || "") : "",
        ]);
      });
    }
  }
  return out;
}

// Standard CSV quoting: wrap in quotes when the value holds a comma, a quote
// or a newline, and double any quote inside. Matches Python's csv.writer.
function quote(value) {
  const text = String(value ?? "");
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function build(days) {
  return [HEADER, ...rowsFor(days)].map((row) => row.map(quote).join(",")).join("\n") + "\n";
}

export const filename = (today) => `workout-log-${today}.csv`;

// Share on a phone where it is offered, download everywhere else. Returns how
// it went so the caller can say so.
export async function exportCsv(days, today, { navigatorRef = navigator, documentRef = document } = {}) {
  if (!days || !days.some((d) => L.exercisesInOrder(d).length)) return { empty: true };

  const name = filename(today);
  const text = build(days);
  const file = typeof File === "function" ? new File([text], name, { type: "text/csv" }) : null;

  // canShare({files}) is the only reliable test: Safari offers navigator.share
  // for links while refusing files.
  if (file && navigatorRef.canShare && navigatorRef.canShare({ files: [file] })) {
    try {
      await navigatorRef.share({ files: [file], title: "Workout Log" });
      return { shared: true };
    } catch (err) {
      // Dismissing the share sheet is not a failure; fall through to download.
      if (err && err.name === "AbortError") return { cancelled: true };
    }
  }

  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const link = documentRef.createElement("a");
  link.href = url;
  link.download = name;
  documentRef.body.append(link);
  link.click();
  link.remove();
  // Revoked on the next tick: Safari needs the URL alive while the click runs.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return { downloaded: true };
}
