"""The refresh-token cookie routes: /v1/auth/session, /refresh, /signout.

These are the only routes that start from a cookie, so the Origin check and
the cookie's attributes are the security boundary under test.
"""

from __future__ import annotations

import logging

import pytest

from conftest import APP_ORIGIN

SAME_ORIGIN = {"Origin": APP_ORIGIN}


def _cookie(token: str) -> dict:
    # TestClient talks plain http, so it would never send a Secure cookie on
    # its own. Send the header directly, as the browser would over HTTPS.
    return {**SAME_ORIGIN, "Cookie": f"wl_rt={token}"}


def _set_cookie(response) -> str:
    headers = response.headers.get_list("set-cookie")
    assert len(headers) == 1, headers
    return headers[0]


def _cookie_value(response) -> str:
    return _set_cookie(response).split(";", 1)[0].removeprefix("wl_rt=").strip('"')


# ---------------------------------------------------------------- origin

@pytest.mark.parametrize("path", ["/v1/auth/session", "/v1/auth/refresh", "/v1/auth/signout"])
@pytest.mark.parametrize("origin", [None, "https://evil.example", APP_ORIGIN + ".evil.example",
                                    "http://app.example.test", "null"])
def test_every_auth_route_refuses_a_foreign_or_missing_origin(api, sessions, path, origin):
    token = sessions.issue("user-1")
    headers = {"Cookie": f"wl_rt={token}"}
    if origin is not None:
        headers["Origin"] = origin
    response = api().post(path, headers=headers, json={"refreshToken": token})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bad_origin"
    assert token in sessions.live, "a refused request must not touch the token"


def test_with_app_origin_unset_everything_is_refused(api, sessions, monkeypatch):
    monkeypatch.delenv("APP_ORIGIN")
    response = api().post("/v1/auth/refresh", headers=_cookie(sessions.issue("user-1")))
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/v1/auth/session", "/v1/auth/refresh", "/v1/auth/signout"])
def test_auth_routes_are_post_only(api, sessions, path):
    assert api().get(path, headers=SAME_ORIGIN).status_code == 405


# --------------------------------------------------------------- session

def test_session_swaps_the_token_and_sets_a_locked_down_cookie(api, sessions):
    first = sessions.issue("user-1")
    response = api().post("/v1/auth/session", headers=SAME_ORIGIN, json={"refreshToken": first})

    assert response.status_code == 200
    assert response.json() == {"ok": True}, "no token goes back to JavaScript"
    assert response.headers["cache-control"] == "no-store"
    cookie = _set_cookie(response)
    attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert {"httponly", "secure", "samesite=strict", "path=/v1/auth",
            f"max-age={90 * 86400}"} <= attributes
    assert first not in sessions.live, "the token the app saw is rotated away"
    assert sessions.live[_cookie_value(response)] == "user-1"


def test_session_refuses_another_users_refresh_token(api, sessions):
    """Planting your own session in someone else's browser."""
    planted = sessions.issue("user-2")
    response = api("user-1").post("/v1/auth/session", headers=SAME_ORIGIN,
                                  json={"refreshToken": planted})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "session_mismatch"
    assert "set-cookie" not in response.headers
    assert not any(s == "user-2" for s in sessions.live.values()), "the swapped token is revoked"


def test_session_with_a_dead_token_is_401(api, sessions):
    response = api().post("/v1/auth/session", headers=SAME_ORIGIN,
                          json={"refreshToken": "never-issued"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_expired"


def test_session_body_is_strict(api, sessions):
    client = api()
    assert client.post("/v1/auth/session", headers=SAME_ORIGIN, json={}).status_code == 422
    assert client.post("/v1/auth/session", headers=SAME_ORIGIN,
                       json={"refreshToken": "x", "sub": "user-2"}).status_code == 422
    assert client.post("/v1/auth/session", headers=SAME_ORIGIN,
                       json={"refreshToken": "x" * 9000}).status_code == 422


# --------------------------------------------------------------- refresh

def test_refresh_returns_tokens_and_rotates_the_cookie(api, sessions):
    old = sessions.issue("user-1")
    response = api().post("/v1/auth/refresh", headers=_cookie(old))

    assert response.status_code == 200
    body = response.json()
    assert body == {"accessToken": "at-user-1", "idToken": body["idToken"], "expiresIn": 900}
    assert "refreshToken" not in body
    assert response.headers["cache-control"] == "no-store"
    new = _cookie_value(response)
    assert new != old and old not in sessions.live and sessions.live[new] == "user-1"


def test_refresh_without_a_cookie_is_401(api, sessions):
    response = api().post("/v1/auth/refresh", headers=SAME_ORIGIN)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "signed_out"


def test_refresh_with_a_dead_cookie_is_401_and_clears_it(api, sessions):
    response = api().post("/v1/auth/refresh", headers=_cookie("revoked-long-ago"))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_expired"
    cookie = _set_cookie(response).lower()
    assert "max-age=0" in cookie and "path=/v1/auth" in cookie


def test_a_copied_cookie_stops_working_once_the_app_refreshes(api, sessions):
    stolen = sessions.issue("user-1")
    assert api().post("/v1/auth/refresh", headers=_cookie(stolen)).status_code == 200
    assert api().post("/v1/auth/refresh", headers=_cookie(stolen)).status_code == 401


# --------------------------------------------------------------- sign out

def test_signout_revokes_this_device_and_clears_the_cookie(api, sessions):
    here = sessions.issue("user-1")
    elsewhere = sessions.issue("user-1")
    response = api().post("/v1/auth/signout", headers=_cookie(here))

    assert response.status_code == 200
    assert "max-age=0" in _set_cookie(response).lower()
    assert here in sessions.revoked
    assert elsewhere in sessions.live, "other devices stay signed in"


def test_signout_without_a_cookie_still_clears_it(api, sessions):
    response = api().post("/v1/auth/signout", headers=SAME_ORIGIN)
    assert response.status_code == 200
    assert "max-age=0" in _set_cookie(response).lower()


def test_signout_everywhere_revokes_every_device(api, sessions):
    here = sessions.issue("user-1")
    elsewhere = sessions.issue("user-1")
    other_user = sessions.issue("user-2")
    response = api().post("/v1/auth/signout", json={"everywhere": True},
                          headers={**_cookie(here), "Authorization": "Bearer at-user-1"})

    assert response.status_code == 200
    assert here not in sessions.live and elsewhere not in sessions.live
    assert other_user in sessions.live


@pytest.mark.parametrize("authorization", [None, "Bearer forged", "Basic at-user-1"])
def test_signout_everywhere_needs_a_real_access_token(api, sessions, authorization):
    here = sessions.issue("user-1")
    headers = _cookie(here)
    if authorization:
        headers["Authorization"] = authorization
    response = api().post("/v1/auth/signout", json={"everywhere": True}, headers=headers)
    assert response.status_code == 401
    assert sessions.signed_out_everywhere == []


# ------------------------------------------------------------------- logs

def test_tokens_never_reach_the_logs(api, sessions, caplog):
    secret = sessions.issue("user-1")
    with caplog.at_level(logging.DEBUG):
        response = api().post("/v1/auth/refresh", headers=_cookie(secret))
        rotated = _cookie_value(response)
        api().post("/v1/auth/session", headers=SAME_ORIGIN, json={"refreshToken": rotated})
    text = caplog.text
    assert secret not in text and rotated not in text and "at-user-1" not in text


# ----------------------------------------------- the Cognito wrapper itself

@pytest.fixture
def stubbed():
    import boto3
    from botocore.stub import Stubber
    from workoutlog.sessions import CognitoSessions

    client = boto3.client("cognito-idp", region_name="us-east-1")
    with Stubber(client) as stub:
        yield CognitoSessions(client=client, client_id="client-123"), stub


def test_rotate_maps_cognitos_reply(stubbed):
    wrapper, stub = stubbed
    stub.add_response("get_tokens_from_refresh_token",
                      {"AuthenticationResult": {"AccessToken": "a", "IdToken": "i",
                                                "RefreshToken": "r2", "ExpiresIn": 900}},
                      {"RefreshToken": "r1", "ClientId": "client-123"})
    assert wrapper.rotate("r1") == {"accessToken": "a", "idToken": "i",
                                    "refreshToken": "r2", "expiresIn": 900}


@pytest.mark.parametrize("code", ["NotAuthorizedException", "RefreshTokenReuseException",
                                  "UserNotFoundException"])
def test_a_dead_token_is_session_expired(stubbed, code):
    from workoutlog.sessions import SessionExpired
    wrapper, stub = stubbed
    stub.add_client_error("get_tokens_from_refresh_token", service_error_code=code)
    with pytest.raises(SessionExpired):
        wrapper.rotate("r1")


@pytest.mark.parametrize("code", ["InternalErrorException", "TooManyRequestsException"])
def test_a_cognito_outage_is_502(stubbed, code):
    from workoutlog.errors import ApiError
    wrapper, stub = stubbed
    stub.add_client_error("get_tokens_from_refresh_token", service_error_code=code)
    with pytest.raises(ApiError) as caught:
        wrapper.rotate("r1")
    assert (caught.value.status, caught.value.code) == (502, "auth_unavailable")


def test_revoking_an_already_dead_token_is_fine(stubbed):
    wrapper, stub = stubbed
    stub.add_client_error("revoke_token", service_error_code="NotAuthorizedException")
    wrapper.revoke("r1")


@pytest.mark.parametrize("token,sub", [
    ("h." + __import__("base64").urlsafe_b64encode(b'{"sub":"abc"}').decode().rstrip("=") + ".s", "abc"),
    ("not-a-jwt", ""), ("h.%%%.s", ""), ("h.bnVsbA.s", ""),
])
def test_token_sub_never_raises(token, sub):
    from workoutlog.sessions import token_sub
    assert token_sub(token) == sub
