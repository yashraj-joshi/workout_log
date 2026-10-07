"""Speech to text, with the gym's own vocabulary as a hint.

Keywords are the exercise names this user already uses, most recent first,
then the common names from the catalog. Without them "Svend press" and
"bird dog" come back as something else entirely.
"""

from __future__ import annotations

import io
import os

from ..catalog import common_names
from .openai_client import ai_errors, client
from .prompts import TRANSCRIBE_PROMPT

# The API caps the keyword list; keeping well inside it costs nothing and
# leaves the most useful names (the ones actually used) at the front.
MAX_KEYWORDS = 100


def keywords_for(recent_names: list[str]) -> list[str]:
    seen: list[str] = []
    for name in [*recent_names, *common_names()]:
        cleaned = (name or "").strip()
        if cleaned and cleaned.lower() not in {s.lower() for s in seen}:
            seen.append(cleaned)
    return seen[:MAX_KEYWORDS]


def transcribe(audio: bytes, mime: str, recent_names: list[str]) -> tuple[str, float]:
    """Returns (text, seconds of audio). The audio is never written to disk or
    logged. The duration comes back from the API, so the cost metric uses the
    real length rather than a guess from the byte count."""
    # The SDK takes a file-like object; the name only tells it the container.
    suffix = "mp4" if "mp4" in mime else "webm"
    handle = io.BytesIO(audio)
    handle.name = f"recording.{suffix}"

    with ai_errors():
        result = client().audio.transcriptions.create(
            file=handle,
            model=os.environ.get("TRANSCRIBE_MODEL", "gpt-transcribe"),
            language="en",
            prompt=TRANSCRIBE_PROMPT,
            keywords=keywords_for(recent_names),
        )
    usage = getattr(result, "usage", None)
    seconds = float(getattr(usage, "seconds", 0) or 0) if usage else 0.0
    return (getattr(result, "text", "") or "").strip(), seconds
