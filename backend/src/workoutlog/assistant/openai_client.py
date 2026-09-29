"""The only module in the codebase that reads the OpenAI key.

Only AssistantFunction has ssm:GetParameter on the key's parameter, so
ApiFunction cannot reach it even by importing this file.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager

import boto3
import openai
from openai import OpenAI

from ..errors import ApiError

log = logging.getLogger("workoutlog")

_key: str | None = None
_client: OpenAI | None = None


def api_key() -> str:
    """Read once per cold start and keep it in memory; a SSM call on every
    request would add latency and cost for a value that never changes."""
    global _key
    if _key is None:
        name = os.environ.get("OPENAI_KEY_PARAM", "/workout-log/openai-api-key")
        ssm = boto3.client("ssm")
        _key = ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    return _key


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=api_key(),
            # The HTTP API gives up at 30 s, so never wait longer than the
            # request itself can live.
            timeout=float(os.environ.get("OPENAI_TIMEOUT", "22")),
            max_retries=1,
        )
    return _client


def reset_cache() -> None:
    """Tests only. Deployed code never calls this."""
    global _key, _client
    _key = None
    _client = None


def _is_quota(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code == "insufficient_quota":
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and (body.get("error") or {}).get("code") == "insufficient_quota":
        return True
    return "insufficient_quota" in str(exc)


@contextmanager
def ai_errors():
    """Turn SDK failures into the messages the app shows. Nothing is logged but
    the exception type: the payload holds transcripts."""
    try:
        yield
    except (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError) as exc:
        log.warning("openai unavailable: %s", type(exc).__name__)
        raise ApiError(502, "ai_unavailable", "Couldn't reach the AI. Nothing was logged.") from exc
    except openai.RateLimitError as exc:
        if _is_quota(exc):
            raise ApiError(402, "openai_quota",
                           "OpenAI credit ran out. Top up the account to keep using voice.") from exc
        raise ApiError(502, "ai_unavailable", "The AI is busy. Nothing was logged.") from exc
    except openai.AuthenticationError as exc:
        log.error("openai rejected the key")
        raise ApiError(502, "ai_unavailable", "Couldn't reach the AI. Nothing was logged.") from exc
    except openai.APIStatusError as exc:
        if _is_quota(exc):
            raise ApiError(402, "openai_quota",
                           "OpenAI credit ran out. Top up the account to keep using voice.") from exc
        log.warning("openai status %s", getattr(exc, "status_code", "?"))
        raise ApiError(502, "ai_unavailable", "Couldn't reach the AI. Nothing was logged.") from exc
