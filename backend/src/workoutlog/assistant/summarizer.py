"""Turns the computed facts into the day summary."""

from __future__ import annotations

import os

from ..models import MAX_SUMMARY
from .openai_client import ai_errors, client
from .prompts import SUMMARY_SYSTEM


def write_summary(facts_text: str) -> str:
    """Responses API with store=false: the log never leaves as stored state on
    OpenAI's side, only as the request itself."""
    with ai_errors():
        response = client().responses.create(
            model=os.environ.get("ASSISTANT_MODEL", "gpt-6-luna"),
            instructions=SUMMARY_SYSTEM,
            input=facts_text,
            reasoning={"effort": "low"},
            max_output_tokens=int(os.environ.get("SUMMARY_MAX_TOKENS", "400")),
            store=False,
        )
    text = (getattr(response, "output_text", "") or "").strip()
    return text[:MAX_SUMMARY]
