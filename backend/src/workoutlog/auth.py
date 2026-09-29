"""Identity comes from the API Gateway JWT authorizer, never from the request
body. There is no switch that bypasses this; tests override the dependency."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from fastapi import Request

from .errors import ApiError

AI_GROUPS = ("ai-users", "admins")


@dataclass(frozen=True)
class User:
    sub: str
    email: str = ""
    groups: tuple[str, ...] = field(default=())

    @property
    def can_use_ai(self) -> bool:
        return any(g in AI_GROUPS for g in self.groups)

    @property
    def is_admin(self) -> bool:
        return "admins" in self.groups


def parse_groups(raw) -> tuple[str, ...]:
    """HTTP API hands cognito:groups over as a real list or as the string
    "[admins ai-users]", depending on the path the claim took."""
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(g).strip() for g in raw if str(g).strip())
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return tuple(part for part in text.replace(",", " ").split() if part)


def claims_from_event(event: dict | None) -> dict:
    try:
        return event["requestContext"]["authorizer"]["jwt"]["claims"] or {}
    except (KeyError, TypeError):
        return {}


def current_user(request: Request) -> User:
    """FastAPI dependency. A missing sub means the authorizer did not run, which
    is a deployment fault, so it fails closed."""
    claims = claims_from_event(request.scope.get("aws.event"))
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise ApiError(401, "unauthorized", "Sign in to continue.")
    return User(
        sub=sub,
        email=str(claims.get("email") or ""),
        groups=parse_groups(claims.get("cognito:groups")),
    )


def require_ai(user: User) -> None:
    if not user.can_use_ai:
        raise ApiError(403, "ai_not_enabled",
                       "AI features are not enabled for this account.")


def git_commit() -> str:
    return os.environ.get("GIT_COMMIT", "unknown")
