"""The assistant loop: context in, tool calls out, one structured reply back.

Bounded on purpose. At most MAX_MODEL_CALLS round trips and a wall-clock
deadline well inside API Gateway's 30 seconds, because a request that runs
past it is billed and then thrown away.
"""

from __future__ import annotations

import json
import logging
import os
import time

from .. import logic
from ..errors import ApiError
from . import tools
from .openai_client import ai_errors, client
from .prompts import ASSISTANT_SYSTEM

log = logging.getLogger("workoutlog")

MAX_MODEL_CALLS = 5
DEADLINE_SECONDS = 25.0

# The shape of the final answer. Strict, so the three fields are always there
# and the app never has to guess what it got.
REPLY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reply": {"type": "string", "description": "1-2 lines, what was logged or the answer."},
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Anything you had to read into it. Empty when there was nothing.",
        },
        "question": {
            "type": ["string", "null"],
            "description": "One question, for what is missing from what you logged, or when you cannot tell what the user meant.",
        },
    },
    "required": ["reply", "assumptions", "question"],
}


def day_context(day: dict | None, date: str) -> str:
    if not day or not day.get("exercises"):
        return f"{date}: nothing logged yet."
    rows = []
    for index, (key, ex) in enumerate(sorted((day.get("exercises") or {}).items()), start=1):
        rows.append(f"  #{index} key={key} {ex.get('exercise')} ({logic.group_of(ex)}): {logic.compact_line(ex)}")
    head = [f"{date}:"]
    place = logic.place_of(day)
    head.append(f"  where: {place['place']}" + (" (guessed)" if place["guessed"] else ""))
    if day.get("bodyweight"):
        head.append(f"  bodyweight: {logic.fmt_num(day['bodyweight'])} lb")
    if day.get("notes"):
        head.append(f"  notes: {day['notes']}")
    if day.get("summaryGeneratedAt"):
        head.append("  summary: already written for this day")
    return "\n".join([*head, *rows])


def build_context(*, today: str, timezone: str, date: str, day: dict | None,
                  known_names: list[str], turns: list[dict]) -> str:
    """Everything the model needs that is not in the user's sentence."""
    lines = [
        f"Today is {logic.fmt_date_long(today)} ({today}). Timezone: {timezone}.",
        f"Log to {date} unless the user names a day (\"yesterday\", \"Monday\"), and then "
        f"use the date they mean. \"Today\" always means {today}.",
        "",
        "That day right now:",
        day_context(day, date),
    ]
    if known_names:
        lines += ["", f"Exercise names the user already uses: {', '.join(known_names[:40])}."]
    if turns:
        lines += ["", "The last few exchanges about this date:"]
        for turn in turns:
            if turn.get("transcript"):
                lines.append(f"  the user said: {turn['transcript']}")
            if turn.get("reply"):
                lines.append(f"  you replied: {turn['reply']}")
            if turn.get("question"):
                lines.append(f"  you asked: {turn['question']}")
    return "\n".join(lines)


def _function_calls(response) -> list:
    return [item for item in (getattr(response, "output", None) or [])
            if getattr(item, "type", None) == "function_call"]


def _final(response) -> dict:
    """The structured reply. A model that stops early (hit the token cap, or a
    refusal) leaves no parsable text, which is a 502 rather than a silent
    half-answer."""
    text = (getattr(response, "output_text", "") or "").strip()
    if not text:
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        log.warning("assistant produced no output (%s)", reason or getattr(response, "status", "?"))
        raise ApiError(502, "ai_unavailable", "Couldn't reach the AI. Nothing was logged.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Not supposed to happen with a strict schema, but a plain sentence is
        # still more use to the person than an error.
        return {"reply": text[:500], "assumptions": [], "question": None}
    return {
        "reply": str(data.get("reply") or "").strip(),
        "assumptions": [str(a) for a in (data.get("assumptions") or []) if str(a).strip()],
        "question": (str(data["question"]).strip() or None) if data.get("question") else None,
    }


def run(*, session: tools.Session, context: str, said: str, create=None) -> dict:
    """Drives the conversation until the model stops calling tools.

    `create` is the Responses API call, injected so tests never reach OpenAI.
    Returns {reply, assumptions, question, modelCalls, inputTokens, outputTokens}.
    """
    create = create or (lambda **kw: client().responses.create(**kw))
    started = time.monotonic()
    conversation: list[dict] = [
        {"role": "developer", "content": context},
        {"role": "user", "content": said},
    ]
    input_tokens = output_tokens = 0

    for call in range(1, MAX_MODEL_CALLS + 1):
        left = DEADLINE_SECONDS - (time.monotonic() - started)
        if left <= 1:
            # Out of time with tool calls still coming. Whatever was written is
            # written; say so rather than pretending the turn finished.
            raise ApiError(504, "ai_timeout", "That took too long. Check the day to see what was logged.")

        with ai_errors():
            response = create(
                model=os.environ.get("ASSISTANT_MODEL", "gpt-6-luna"),
                instructions=ASSISTANT_SYSTEM,
                input=conversation,
                tools=tools.tool_params(),
                text={"format": {"type": "json_schema", "name": "assistant_reply",
                                 "schema": REPLY_SCHEMA, "strict": True}},
                reasoning={"effort": "low"},
                max_output_tokens=int(os.environ.get("ASSISTANT_MAX_TOKENS", "900")),
                store=False,
                timeout=max(1.0, left),
            )

        usage = getattr(response, "usage", None)
        if usage:
            input_tokens += getattr(usage, "input_tokens", 0) or 0
            output_tokens += getattr(usage, "output_tokens", 0) or 0

        calls = _function_calls(response)
        if not calls:
            return {**_final(response), "modelCalls": call,
                    "inputTokens": input_tokens, "outputTokens": output_tokens}

        # Carry the model's own items forward, then one output per call.
        conversation += [item.model_dump(exclude_none=True) for item in (response.output or [])]
        for item in calls:
            conversation.append({
                "type": "function_call_output",
                "call_id": item.call_id,
                "output": session.run(item.name, item.arguments),
            })

    # Out of round trips. The writes already happened, so this is not a failure
    # of the turn, just the end of it.
    raise ApiError(504, "ai_busy", "That needed too many steps. Check the day to see what was logged.")
