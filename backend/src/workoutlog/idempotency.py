"""Running a write at most once per Idempotency-Key.

Two shapes, because the routes have two shapes:

- `run_once` is for a write that fits in one transaction. The key's record
  joins that transaction, so the write and the record land together or not
  at all.
- `claim` / `finish` / `release` is for the assistant, which makes several
  writes and paid model calls and cannot fit in one transaction. It reserves
  the key first, does the work, then marks the key done.

Either way a repeat of the same request gets the first response back instead
of doing the work twice.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from .errors import ApiError
from .repo import Repo, RequestRecord, RequestReplayed

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def valid_uuid(value: str, what: str) -> str:
    if not _UUID_RE.match(value):
        raise ApiError(400, "validation_error", f"{what} should be a UUID.")
    return value.lower()


def idempotency_key(
    value: str | None = Header(default=None, alias="Idempotency-Key", max_length=64),
) -> str | None:
    """Optional. The app creates one per action and sends it unchanged on every
    retry of that action, so a reply lost on a weak connection can't log twice."""
    return None if value is None else valid_uuid(value.strip(), "Idempotency-Key")


def request_hash(request: Request, body) -> str:
    """What was asked, so a key reused for a different request is caught. The
    validated body, not the raw bytes: key order and spacing don't matter."""
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{request.method} {request.url.path}\n{canonical}".encode()).hexdigest()


def replay(stored: dict, expected_hash: str) -> JSONResponse:
    if stored.get("requestHash") != expected_hash:
        raise ApiError(422, "idempotency_key_reused",
                       "That Idempotency-Key was already used for a different request.")
    if stored.get("status") != "done":
        raise ApiError(409, "request_in_progress", "That request is still running.",
                       headers={"Retry-After": "2"})
    return JSONResponse(content=stored_response(stored), status_code=int(stored["httpStatus"]))


def stored_response(stored: dict) -> dict:
    """The saved answer, exactly as it was sent the first time. It is kept as
    JSON because DynamoDB maps drop None, and a null field that quietly
    vanished on a replay would be a different answer."""
    raw = stored.get("response")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw or {}


def run_once(repo: Repo, sub: str, key: str | None, request: Request, body, status: int,
             run: Callable[[RequestRecord | None], dict]):
    """A single-transaction write, at most once per key. A repeat gets the
    first response back: no write, no dataVersion bump."""
    if key is None:
        return run(None)
    record = RequestRecord(key, request_hash(request, body), status)
    stored = repo.get_request(sub, key)
    if stored:
        return replay(stored, record.request_hash)
    try:
        return run(record)
    except RequestReplayed:
        # Another copy of this request committed between our check and our write.
        return replay(repo.get_request(sub, key) or {}, record.request_hash)


class Claim:
    """A key held while work that spans several writes runs.

    `taken` is the stored record when someone else already has the key, in
    which case the caller replays it instead of doing anything.
    """

    def __init__(self, repo: Repo, sub: str, key: str | None, expected_hash: str | None):
        self.repo = repo
        self.sub = sub
        self.key = key
        self.hash = expected_hash
        self.taken: dict | None = repo.claim_request(sub, key, expected_hash) if key else None

    def finish(self, response: dict[str, Any], http_status: int = 200) -> None:
        if self.key:
            self.repo.finish_request(self.sub, self.key, response, http_status)

    def release(self) -> None:
        """Give the key back, so a retry runs fresh. Only safe when nothing
        was written."""
        if self.key:
            self.repo.release_request(self.sub, self.key)
