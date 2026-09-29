"""Claim reading. These call the real dependency rather than the override, so
the path from the API Gateway event to a User is covered."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from workoutlog.auth import User, current_user, parse_groups, require_ai
from workoutlog.errors import ApiError


def _request(claims: dict | None) -> Request:
    event = {"requestContext": {"authorizer": {"jwt": {"claims": claims}}}} if claims else {}
    return Request({"type": "http", "method": "GET", "path": "/v1/days",
                    "headers": [], "aws.event": event})


@pytest.mark.parametrize("raw,expected", [
    ("[admins ai-users]", ("admins", "ai-users")),      # HTTP API's string form
    (["admins", "ai-users"], ("admins", "ai-users")),   # a real list
    ("ai-users", ("ai-users",)),
    ("[ai-users]", ("ai-users",)),
    ("admins,ai-users", ("admins", "ai-users")),
    (None, ()),
    ("[]", ()),
])
def test_parse_groups(raw, expected):
    assert parse_groups(raw) == expected


def test_current_user_reads_the_jwt_claims():
    user = current_user(_request({"sub": "abc", "email": "y@example.com",
                                  "cognito:groups": "[admins]"}))
    assert user == User("abc", "y@example.com", ("admins",))
    assert user.can_use_ai and user.is_admin


def test_missing_claims_fail_closed():
    for claims in (None, {}, {"sub": ""}, {"email": "y@example.com"}):
        with pytest.raises(ApiError) as caught:
            current_user(_request(claims))
        assert caught.value.status == 401


def test_require_ai():
    require_ai(User("a", groups=("ai-users",)))
    require_ai(User("a", groups=("admins",)))
    with pytest.raises(ApiError) as caught:
        require_ai(User("a", groups=("readers",)))
    assert caught.value.status == 403
    assert caught.value.code == "ai_not_enabled"


def test_there_is_no_auth_bypass_in_the_source():
    """A guard against someone adding a "skip auth locally" flag later."""
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "src" / "workoutlog"
    banned = ("SKIP_AUTH", "DISABLE_AUTH", "BYPASS_AUTH", "NO_AUTH")
    for path in source.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for flag in banned:
            assert flag not in text, f"{path.name} mentions {flag}"
