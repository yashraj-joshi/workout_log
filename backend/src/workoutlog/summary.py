"""Facts for the "Done for today" summary.

Code computes every number here. The model only turns this block into two or
three sentences, so it can't invent a total or a comparison.
"""

from __future__ import annotations

from . import logic


def day_facts(day: dict, history: list[dict]) -> dict:
    """`history` is every earlier day, used only to compare each exercise's top
    set with its most recent previous session."""
    date = day.get("date", "")
    earlier = sorted([d for d in history if (d.get("date") or "") < date],
                     key=lambda d: d.get("date") or "")
    totals = logic.day_totals(day)
    place = logic.place_of(day)

    rows = []
    for ex in logic.exercises_in_order(day):
        name = ex.get("exercise", "")
        top = logic.top_set(ex)
        sessions = logic.exercise_sessions(earlier, name)
        previous = None
        for candidate in reversed(sessions):
            if candidate.get("topSet") and top and candidate["topSet"]["kind"] == top["kind"]:
                previous = candidate
                break
        rows.append({
            "exercise": name,
            "group": logic.group_of(ex),
            "muscles": logic.muscles_of(ex),
            "sets": len(ex.get("sets") or []),
            "compact": logic.compact_line(ex),
            "isCardio": logic.is_cardio(ex),
            "topSet": top["label"] if top else None,
            "previousDate": previous["date"] if previous else None,
            "previousTopSet": previous["topSet"]["label"] if previous else None,
            "change": logic.change_label(top, previous["topSet"] if previous else None),
        })

    return {
        "date": date,
        "weekday": logic.fmt_date_long(date) if date else "",
        "place": place["place"],
        "placeGuessed": place["guessed"],
        "totals": totals,
        "setsPerMuscle": logic.sets_per_muscle([day]),
        "missingAreas": logic.missing_areas([day]),
        "exercises": rows,
        "notes": day.get("notes"),
        "bodyweight": day.get("bodyweight"),
    }


def facts_text(facts: dict) -> str:
    """A compact block for the model. Plain lines beat JSON here: fewer tokens
    and the model copies the numbers more reliably."""
    totals = facts["totals"]
    reps = totals["reps"]
    reps_text = (f"{logic.fmt_num(reps['low'])}{logic.EN_DASH}{logic.fmt_num(reps['high'])}"
                 if reps["hasRange"] else logic.fmt_num(reps["low"]))

    lines = [
        f"Date: {facts['weekday']} ({facts['date']})",
        f"Where: {facts['place']}" + (" (guessed)" if facts["placeGuessed"] else ""),
        f"Totals: {totals['exercises']} exercises, {totals['sets']} sets, {reps_text} reps",
    ]
    if totals["cardioMinutes"]:
        cardio = f"Cardio: {logic.fmt_num(totals['cardioMinutes'])} min"
        if totals["cardioDistance"]:
            cardio += f", {logic.fmt_num(totals['cardioDistance'])} mi"
        lines.append(cardio)
    if facts["bodyweight"]:
        lines.append(f"Bodyweight: {logic.fmt_num(facts['bodyweight'])} lb")
    if facts["notes"]:
        lines.append(f"How it felt: {facts['notes']}")

    if facts["setsPerMuscle"]:
        muscles = ", ".join(f"{r['muscle']} {r['sets']}" for r in facts["setsPerMuscle"][:8])
        lines.append(f"Sets per muscle: {muscles}")
    if facts["missingAreas"]:
        lines.append(f"No direct work: {', '.join(facts['missingAreas'])}")

    lines.append("Exercises:")
    for row in facts["exercises"]:
        line = f"- {row['exercise']} ({row['group']}): {row['compact']}"
        if row["topSet"]:
            line += f"; top set {row['topSet']}"
        if row["previousTopSet"]:
            line += (f"; last time {row['previousDate']} was {row['previousTopSet']}"
                     f" ({row['change']})")
        else:
            line += "; first time logged"
        lines.append(line)
    return "\n".join(lines)
