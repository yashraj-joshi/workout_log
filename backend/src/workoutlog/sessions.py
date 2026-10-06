"""The refresh-token cookie: /v1/auth/session, /v1/auth/refresh, /v1/auth/signout.

The refresh token lasts 90 days, so the app's JavaScript never keeps it. It
lives in an HttpOnly, Secure, SameSite=Strict cookie scoped to /v1/auth, and
every use swaps it for a new one (Cognito refresh-token rotation).

These are the only routes that start from a cookie rather than a JWT the
authorizer checked, so each one also requires the request's Origin to be the
app's own. A cookie the browser sends on its own is what cross-site request
forgery abuses; SameSite=Strict, POST-only and the Origin check together shut
that out.
"""

from __future__ import annotations

import base64
import json
import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .auth import User, current_user
from .errors import ApiError

log = logging.getLogger("workoutlog")

COOKIE = "wl_rt"
COOKIE_PATH = "/v1/auth"
MAX_TOKEN = 8192

# Errors that mean "this refresh token no longer works": expired, revoked,
# reused after the rotation grace period, or its user was deleted.
_SIGNED_OUT = {"NotAuthorizedException", "RefreshTokenReuseException", "UserNotFoundException"}


class SessionExpired(Exception):
    """The token was rejected. The caller clears the cookie and signs in again."""


class CognitoSessions:
    """The three Cognito calls these routes make. None needs IAM permissions:
    each is authorized by the token it carries, plus the app client ID."""

    def __init__(self, client=None, client_id: str | None = None):
        self._client = client or boto3.client("cognito-idp")
        self._client_id = client_id or os.environ.get("COGNITO_CLIENT_ID", "")

    def rotate(self, refresh_token: str) -> dict:
        """New access, ID and refresh tokens. The old refresh token stops
        working 30 seconds later, which covers a retry after a lost reply."""
        result = self._call("get_tokens_from_refresh_token",
                            RefreshToken=refresh_token, ClientId=self._client_id)
        tokens = result["AuthenticationResult"]
        return {
            "accessToken": tokens["AccessToken"],
            "idToken": tokens["IdToken"],
            "refreshToken": tokens.get("RefreshToken"),
            "expiresIn": int(tokens.get("ExpiresIn") or 0),
        }

    def revoke(self, refresh_token: str) -> None:
        """Best effort: signing out must clear the cookie even if Cognito has
        already forgotten the token."""
        try:
            self._call("revoke_token", Token=refresh_token, ClientId=self._client_id)
        except SessionExpired:
            pass

    def sign_out_everywhere(self, access_token: str) -> None:
        """Revokes every refresh token the user has. Cognito checks the access
        token itself, so a forged one fails here."""
        self._call("global_sign_out", AccessToken=access_token)

    def _call(self, operation: str, **kwargs) -> dict:
        try:
            return getattr(self._client, operation)(**kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in _SIGNED_OUT:
                raise SessionExpired(code) from exc
            # The type and code only: the request carried a token.
            log.warning("cognito %s failed: %s", operation, code or type(exc).__name__)
            raise ApiError(502, "auth_unavailable",
                           "Couldn't reach sign-in. Try again in a moment.") from exc
        except BotoCoreError as exc:
            log.warning("cognito %s failed: %s", operation, type(exc).__name__)
            raise ApiError(502, "auth_unavailable",
                           "Couldn't reach sign-in. Try again in a moment.") from exc


_sessions: CognitoSessions | None = None


def get_sessions() -> CognitoSessions:
    """One client per container. Tests override this with a fake."""
    global _sessions
    if _sessions is None:
        _sessions = CognitoSessions()
    return _sessions


def require_same_origin(request: Request) -> None:
    """Fails closed: with APP_ORIGIN unset, every request is refused, so a
    half-configured deploy can't accept cross-site requests."""
    expected = os.environ.get("APP_ORIGIN", "").strip().rstrip("/")
    origin = (request.headers.get("origin") or "").strip()
    if not expected or origin != expected:
        raise ApiError(403, "bad_origin", "This request must come from the app itself.")


def _max_age() -> int:
    return int(os.environ.get("REFRESH_TOKEN_DAYS", "90")) * 86400


def _respond(content: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(content=content, status_code=status, headers={"Cache-Control": "no-store"})


def _set_cookie(response: JSONResponse, refresh_token: str) -> None:
    response.set_cookie(COOKIE, refresh_token, max_age=_max_age(), path=COOKIE_PATH,
                        secure=True, httponly=True, samesite="strict")


def _clear_cookie(response: JSONResponse) -> None:
    response.set_cookie(COOKIE, "", max_age=0, path=COOKIE_PATH,
                        secure=True, httponly=True, samesite="strict")


def _signed_out(code: str, message: str) -> JSONResponse:
    response = _respond({"error": {"code": code, "message": message}}, status=401)
    _clear_cookie(response)
    return response


def token_sub(jwt: str) -> str:
    """The sub claim of a token Cognito has just returned to us over TLS. Not
    verified, because it never passed through the client; that is the only
    reason reading it unverified is safe."""
    try:
        payload = jwt.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return str(claims.get("sub") or "")
    except (IndexError, ValueError, AttributeError):
        return ""


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refreshToken: str = Field(min_length=1, max_length=MAX_TOKEN)


class SignOutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    everywhere: bool = False


router = APIRouter(prefix="/v1/auth", dependencies=[Depends(require_same_origin)])


@router.post("/session")
def start_session(body: SessionIn, user: User = Depends(current_user),
                  sessions: CognitoSessions = Depends(get_sessions)):
    """Right after SRP sign-in. Swaps the refresh token the app briefly holds
    for a new one, which only the cookie ever sees. The new token must belong
    to the signed-in user, or someone could plant their own session in another
    person's browser."""
    try:
        tokens = sessions.rotate(body.refreshToken)
    except SessionExpired:
        raise ApiError(401, "session_expired", "Sign in again.") from None
    if not tokens["refreshToken"] or token_sub(tokens["idToken"]) != user.sub:
        if tokens["refreshToken"]:
            sessions.revoke(tokens["refreshToken"])
        raise ApiError(403, "session_mismatch", "That session belongs to a different account.")
    response = _respond({"ok": True})
    _set_cookie(response, tokens["refreshToken"])
    return response


@router.post("/refresh")
def refresh(request: Request, sessions: CognitoSessions = Depends(get_sessions)):
    """Cookie in, short-lived access and ID tokens out, plus a rotated cookie."""
    token = request.cookies.get(COOKIE)
    if not token:
        return _signed_out("signed_out", "Sign in to continue.")
    try:
        tokens = sessions.rotate(token)
    except SessionExpired:
        return _signed_out("session_expired", "Your session ended. Sign in again.")
    response = _respond({"accessToken": tokens["accessToken"], "idToken": tokens["idToken"],
                         "expiresIn": tokens["expiresIn"]})
    if tokens["refreshToken"]:
        _set_cookie(response, tokens["refreshToken"])
    return response


@router.post("/signout")
def sign_out(request: Request, body: SignOutIn | None = None,
             sessions: CognitoSessions = Depends(get_sessions)):
    """Revokes this device's refresh token. With everywhere, also revokes every
    other one the user has, which needs a current access token."""
    body = body or SignOutIn()
    access = _bearer(request)
    if body.everywhere and not access:
        raise ApiError(401, "unauthorized", "Sign in to sign out everywhere.")
    if body.everywhere:
        try:
            sessions.sign_out_everywhere(access)
        except SessionExpired:
            raise ApiError(401, "unauthorized", "Sign in to sign out everywhere.") from None
    token = request.cookies.get(COOKIE)
    if token:
        sessions.revoke(token)
    response = _respond({"ok": True})
    _clear_cookie(response)
    return response
