// The Add / Edit exercise dialog: full screen on phones, centred on desktop.
//
// A <dialog> rather than a hand-built overlay, so Escape, the backdrop and the
// focus trap are the browser's job. Validation and the wording of every error
// live in parse.js; this file is the form around them.

import { h, replace, toast } from "../dom.js";
import * as L from "../logic.js";
import { commonNames, groups } from "../catalog.js";
import { buildExercise, matchNames, nameSuggestions, rowsFromSets, typeForName, typeOfSets } from "../parse.js";

const TYPE_LABELS = { weights: "Weights", time: "Time", hold: "Hold" };

// The fields each type shows, and what they are called.
const FIELDS = {
  weights: [{ key: "reps", label: "Reps", mode: "numeric" }, { key: "weight", label: "Weight", mode: "decimal" }],
  time: [{ key: "minutes", label: "Minutes", mode: "decimal" }, { key: "distance", label: "Distance (mi)", mode: "decimal" }],
  hold: [{ key: "seconds", label: "Seconds", mode: "decimal" }],
};

const blankRow = () => ({ reps: "", weight: "", minutes: "", distance: "", seconds: "", note: "" });

export function openEditor({ host, days, today, date, existing, api, actions }) {
  const editing = Boolean(existing);
  const from = existing ? existing.date : null;

  // What the form holds while it is open.
  const state = {
    name: existing ? existing.exercise.exercise : "",
    date: (existing ? existing.date : date) || today,
    group: existing ? L.groupOf(existing.exercise) : "",
    muscles: existing ? L.musclesOf(existing.exercise).join(", ") : "",
    type: existing ? typeOfSets(existing.exercise.sets) : "weights",
    unit: existing ? existing.exercise.unit || "lb" : "lb",
    perHand: Boolean(existing && existing.exercise.perHand),
    notes: existing ? existing.exercise.notes || "" : "",
    rows: existing ? rowsFromSets(existing.exercise.sets) : [blankRow()],
  };
  // Area and muscles fill themselves in from the name until I edit them.
  const touched = { group: editing, muscles: editing };
  let removeArmed = false;
  let saving = false;

  const dialog = h("dialog", { class: "sheet", "aria-label": editing ? "Edit exercise" : "Add exercise" });
  const problem = h("p", { class: "notice bad", role: "alert", hidden: true });
  const rowsBox = h("div", { class: "rows" });
  const sameAs = h("div", { class: "same-as" });
  const perHandBox = h("div", {});

  // ------------------------------------------------------------------ parts

  // Every name worth offering, mine first. Filtered as I type and never shown
  // on focus: a list that opens with the dialog covers the form. Any other
  // name is fine too; the field is free text and the list only helps.
  const allNames = nameSuggestions(days, commonNames());
  let matches = [];
  let active = -1;

  const suggest = h("ul", { id: "ex-name-list", class: "suggest", role: "listbox", "aria-label": "Suggestions", hidden: true });

  const nameInput = h("input", {
    id: "ex-name", class: "box", type: "text", value: state.name,
    role: "combobox", "aria-autocomplete": "list", "aria-controls": "ex-name-list", "aria-expanded": "false",
    autocomplete: "off", autocapitalize: "words", enterkeyhint: "next", maxlength: 80,
  });

  function renderSuggestions() {
    suggest.hidden = !matches.length;
    nameInput.setAttribute("aria-expanded", String(matches.length > 0));
    if (active >= 0) nameInput.setAttribute("aria-activedescendant", `ex-name-opt-${active}`);
    else nameInput.removeAttribute("aria-activedescendant");
    replace(suggest, matches.map((name, i) => h("li", {
      id: `ex-name-opt-${i}`, role: "option", "aria-selected": String(i === active),
      // Keeps focus (and the phone keyboard) in the field, so blur doesn't
      // close the list before the click lands.
      onmousedown: (e) => e.preventDefault(),
      onclick: () => pick(name),
    }, name)));
    if (active >= 0) suggest.children[active].scrollIntoView({ block: "nearest" });
  }

  function showSuggestions(list) {
    matches = list;
    active = -1;
    renderSuggestions();
  }

  function pick(name) {
    nameInput.value = name;
    onNameChange();
    showSuggestions([]);
  }

  nameInput.addEventListener("input", () => {
    onNameChange();
    showSuggestions(matchNames(nameInput.value, allNames));
  });
  nameInput.addEventListener("blur", () => showSuggestions([]));
  nameInput.addEventListener("keydown", (e) => {
    if (!matches.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      // -1 is "nothing highlighted", so the arrows can step back to the text.
      const span = matches.length + 1;
      active = ((active + 1 + (e.key === "ArrowDown" ? 1 : -1) + span) % span) - 1;
      renderSuggestions();
    } else if (e.key === "Enter" && active >= 0) {
      // Picks the name rather than saving the form.
      e.preventDefault();
      pick(matches[active]);
    } else if (e.key === "Escape") {
      // Closes the list, not the dialog.
      e.preventDefault();
      showSuggestions([]);
    }
  });

  function onNameChange() {
    state.name = nameInput.value;
    const found = L.lookup(state.name);
    if (found && !touched.group) {
      state.group = found.group;
      groupSelect.value = found.group;
    }
    if (found && !touched.muscles) {
      state.muscles = found.muscles.join(", ");
      musclesInput.value = state.muscles;
    }
    // Only when adding: on an edit the logged shape is the one I meant.
    if (!editing && !state.rows.some(filled)) {
      const next = typeForName(state.name);
      if (next !== state.type) {
        state.type = next;
        renderType();
      }
    }
    renderSameAs();
  }

  const dateInput = h("input", { id: "ex-date", class: "box", type: "date", value: state.date, required: true });
  dateInput.addEventListener("change", () => {
    state.date = dateInput.value;
    renderSameAs();
  });

  const groupSelect = h("select", { id: "ex-area", class: "box" },
    h("option", { value: "" }, "Pick an area"),
    groups().map((name) => h("option", { value: name, selected: name === state.group || undefined }, name)));
  groupSelect.addEventListener("change", () => {
    touched.group = true;
    state.group = groupSelect.value;
  });

  const musclesInput = h("input", {
    id: "ex-muscles", class: "box", type: "text", value: state.muscles,
    autocomplete: "off", placeholder: "Chest, Triceps", maxlength: 120,
  });
  musclesInput.addEventListener("input", () => {
    touched.muscles = true;
    state.muscles = musclesInput.value;
  });

  const typeSeg = h("div", { class: "seg", role: "group", "aria-label": "Type" });

  function renderType() {
    replace(typeSeg, Object.keys(TYPE_LABELS).map((key) => h("button", {
      type: "button",
      "aria-selected": String(state.type === key),
      onclick: () => { state.type = key; renderType(); },
    }, TYPE_LABELS[key])));
    renderRows();
    replace(perHandBox, state.type === "weights" ? perHandField() : null);
  }

  function perHandField() {
    const box = h("input", { id: "ex-perhand", type: "checkbox", checked: state.perHand || undefined });
    box.addEventListener("change", () => { state.perHand = box.checked; });
    return h("label", { class: "check", for: "ex-perhand" }, box, "Weight is per dumbbell");
  }

  const filled = (row) => Object.entries(row).some(([key, value]) => key !== "note" && String(value).trim());

  function renderRows() {
    const fields = FIELDS[state.type];
    replace(rowsBox,
      // A heading row: once the fields hold numbers, their placeholders are
      // gone and nothing else says which column is which.
      h("div", { class: "row row-head", dataset: { fields: fields.length }, "aria-hidden": "true" },
        h("span", {}),
        fields.map((field) => h("span", { class: "label" }, field.label))),
      state.rows.map((row, i) => h("div", { class: "row", dataset: { fields: fields.length } },
        h("span", { class: "row-num" }, i + 1),
        fields.map((field) => {
          const input = h("input", {
            class: "box", type: "text", inputmode: field.mode, value: row[field.key],
            "aria-label": `Set ${i + 1} ${field.label}`, placeholder: field.label, maxlength: 12,
          });
          input.addEventListener("input", () => { row[field.key] = input.value; });
          return input;
        }),
        (() => {
          const note = h("input", {
            class: "box note", type: "text", value: row.note, maxlength: 80,
            "aria-label": `Set ${i + 1} note`, placeholder: "Note",
          });
          note.addEventListener("input", () => { row.note = note.value; });
          return note;
        })(),
        h("button", {
          class: "row-x", type: "button", "aria-label": `Remove set ${i + 1}`,
          disabled: state.rows.length === 1 || undefined,
          onclick: () => { state.rows.splice(i, 1); renderRows(); },
        }, "×"))),
      h("button", {
        class: "btn add-set", type: "button",
        onclick: () => {
          // Copies the last row, because the next set is usually the same one.
          state.rows.push({ ...state.rows[state.rows.length - 1] });
          renderRows();
          const inputs = rowsBox.querySelectorAll(".row:last-of-type input");
          if (inputs.length) inputs[0].focus();
        },
      }, "+ Add set"));
  }

  // "Same as last time (Sep 21: 3 × 10 @ 40 lb)" — one tap to repeat a session.
  function renderSameAs() {
    const sessions = L.exerciseSessions(days, state.name)
      .filter((s) => s.date !== state.date && s.date <= state.date);
    const last = sessions[sessions.length - 1];
    if (!state.name.trim() || !last) return replace(sameAs);
    replace(sameAs, h("button", {
      class: "linklike", type: "button",
      onclick: () => {
        const ex = last.exercise;
        state.type = typeOfSets(ex.sets);
        state.unit = ex.unit || "lb";
        state.perHand = Boolean(ex.perHand);
        state.rows = rowsFromSets(ex.sets);
        state.group = L.groupOf(ex);
        state.muscles = L.musclesOf(ex).join(", ");
        groupSelect.value = state.group;
        musclesInput.value = state.muscles;
        touched.group = true;
        touched.muscles = true;
        renderType();
      },
    }, `Same as last time (${L.fmtDateShort(last.date, today)}: ${L.compactLine(last.exercise)})`));
  }

  // ----------------------------------------------------------------- saving

  function fail(message) {
    problem.textContent = message;
    problem.hidden = false;
    problem.scrollIntoView({ block: "nearest" });
  }

  async function onSave() {
    if (saving) return;
    problem.hidden = true;
    const built = buildExercise(state);
    if (built.error) return fail(built.error);

    const { exercise } = built;
    const target = built.date;
    saving = true;
    try {
      if (!editing) {
        actions.applied(target, await api.addExercise(target, exercise));
        toast(`Added ${exercise.exercise} to ${L.fmtDateShort(target, today)}.`);
      } else if (target === from) {
        actions.applied(target, await api.putExercise(from, existing.key, exercise));
        toast(`Saved changes to ${exercise.exercise}.`);
      } else {
        // The edit lands first, so what moves is what I just typed. Both days
        // change, so this one leans on the refresh rather than patching state.
        await api.putExercise(from, existing.key, exercise);
        await api.moveExercise(from, existing.key, target);
        toast(`Moved ${exercise.exercise} to ${L.fmtDateShort(target, today)}.`);
      }
      close();
      actions.done(target);
    } catch (err) {
      saving = false;
      if (err.status === 0) return fail("You're offline. Saving needs a connection.");
      // A validation message from the server is more use than a generic one.
      if (err.status === 400 || err.status === 422) return fail(err.message);
      toast("Couldn't save that. Try again.");
    }
  }

  async function onRemove() {
    if (!removeArmed) {
      removeArmed = true;
      removeButton.textContent = "Tap again to remove";
      return;
    }
    try {
      const name = existing.exercise.exercise;
      actions.applied(from, await api.removeExercise(from, existing.key));
      close();
      toast(`Removed ${name}.`);
      actions.done(from);
    } catch (err) {
      toast(err.status === 0 ? "You're offline. This needs a connection." : "Couldn't save that. Try again.");
    }
  }

  const removeButton = h("button", { class: "btn danger", type: "button", onclick: () => onRemove() },
    "Remove exercise");

  function close() {
    dialog.close();
    dialog.remove();
  }

  // ------------------------------------------------------------------ build

  const field = (id, label, control, hint) => h("div", { class: "field" },
    h("label", { class: "label", for: id }, label), control,
    hint ? h("p", { class: "meta" }, hint) : null);

  replace(dialog,
    h("form", { class: "sheet-form", method: "dialog", onsubmit: (e) => { e.preventDefault(); onSave(); } },
      h("div", { class: "sheet-top" },
        h("h2", { class: "dialog-title" }, editing ? "Edit exercise" : "Add exercise"),
        h("button", { class: "btn", type: "button", onclick: () => close() }, "Close")),
      h("div", { class: "sheet-body" },
        problem,
        field("ex-name", "Exercise name", h("div", { class: "combo" }, nameInput, suggest),
          "Pick a suggestion or type your own."),
        sameAs,
        field("ex-date", "Date", dateInput),
        field("ex-area", "Area", groupSelect),
        field("ex-muscles", "Muscles worked, main one first", musclesInput,
          "Comma separated. Leave it alone and it fills itself in from the name."),
        h("div", { class: "field" }, h("p", { class: "label" }, "Type"), typeSeg),
        rowsBox,
        perHandBox,
        field("ex-notes", "Note", (() => {
          const note = h("textarea", { id: "ex-notes", class: "box", rows: 2, maxlength: 300 }, state.notes);
          note.addEventListener("input", () => { state.notes = note.value; });
          return note;
        })())),
      h("div", { class: "sheet-foot" },
        h("button", { class: "btn primary", type: "submit" }, "Save"),
        h("button", { class: "btn", type: "button", onclick: () => close() }, "Cancel"),
        editing ? removeButton : null)));

  renderType();
  renderSameAs();
  host.append(dialog);
  dialog.showModal();
  nameInput.focus();
  // Closing with Escape or the backdrop leaves the element behind otherwise.
  dialog.addEventListener("close", () => dialog.remove());
  return dialog;
}
