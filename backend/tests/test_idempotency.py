"""Idempotency-Key on POST /exercises and POST /move.

The case this exists for: the sets were logged, then the reply was lost on a
weak gym connection, and the app retries. The retry must get the first result
back and log nothing twice.
"""

from __future__ import annotations

import uuid

import pytest
from workoutlog.idempotency import stored_response

DATE = "2026-09-25"
PLANK = {"exercise": "Plank", "sets": [{"seconds": 30}]}


def _key() -> str:
    return str(uuid.uuid4())


def _add(client, key, body=PLANK, date=DATE):
    return client.post(f"/v1/days/{date}/exercises", json=body, headers={"Idempotency-Key": key})


def test_a_retry_gets_the_first_reply_and_logs_nothing_twice(api, repo):
    client = api()
    key = _key()
    first = _add(client, key)
    version = repo.data_version("user-1")

    again = _add(client, key)
    assert again.status_code == first.status_code == 201
    assert again.json() == first.json()
    assert list(repo.get_day("user-1", DATE)["exercises"]) == ["01"]
    assert repo.data_version("user-1") == version, "a replay must not bump the sync ETag"


def test_without_a_key_every_post_is_a_new_exercise(api, repo):
    client = api()
    client.post(f"/v1/days/{DATE}/exercises", json=PLANK)
    client.post(f"/v1/days/{DATE}/exercises", json=PLANK)
    assert list(repo.get_day("user-1", DATE)["exercises"]) == ["01", "02"]


def test_the_same_body_in_a_different_key_order_is_the_same_request(api):
    client = api()
    key = _key()
    _add(client, key, {"exercise": "Plank", "sets": [{"seconds": 30}]})
    assert _add(client, key, {"sets": [{"seconds": 30}], "exercise": "Plank"}).status_code == 201


@pytest.mark.parametrize("body,date", [
    ({"exercise": "Plank", "sets": [{"seconds": 45}]}, DATE),   # different body
    (PLANK, "2026-09-26"),                                       # different day
])
def test_reusing_a_key_for_a_different_request_is_422(api, repo, body, date):
    client = api()
    key = _key()
    _add(client, key)
    response = _add(client, key, body, date)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert repo.get_day("user-1", "2026-09-26") is None


@pytest.mark.parametrize("value", ["", "not-a-uuid", "12345678123456781234567812345678",
                                   "{12345678-1234-1234-1234-123456789012}"])
def test_a_malformed_key_is_400(api, value):
    response = _add(api(), value)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_keys_are_case_insensitive(api, repo):
    client = api()
    key = _key()
    _add(client, key.upper())
    assert _add(client, key).status_code == 201
    assert list(repo.get_day("user-1", DATE)["exercises"]) == ["01"]


def test_one_users_key_never_replays_another_users_request(api, repo):
    key = _key()
    _add(api("user-1"), key)
    response = _add(api("user-2"), key)
    assert response.status_code == 201
    assert repo.get_day("user-2", DATE) is not None
    assert stored_response(repo.get_request("user-2", key))["day"]["date"] == DATE


def test_a_move_retry_moves_once(api, repo):
    client = api()
    client.post(f"/v1/days/{DATE}/exercises", json=PLANK)
    client.post(f"/v1/days/{DATE}/exercises", json=PLANK)
    key = _key()
    move = lambda: client.post(f"/v1/days/{DATE}/exercises/01/move",  # noqa: E731
                               json={"toDate": "2026-09-26"}, headers={"Idempotency-Key": key})
    first = move()
    again = move()
    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    assert list(repo.get_day("user-1", "2026-09-26")["exercises"]) == ["01"]
    assert list(repo.get_day("user-1", DATE)["exercises"]) == ["02"]


def test_a_failed_request_is_not_recorded(api, repo):
    """A 404 wrote nothing, so the same key may run again once it can succeed."""
    client = api()
    key = _key()
    missing = client.post(f"/v1/days/{DATE}/exercises/01/move",
                          json={"toDate": "2026-09-26"}, headers={"Idempotency-Key": key})
    assert missing.status_code == 404
    assert repo.get_request("user-1", key) is None


@pytest.mark.parametrize("second_body,expected", [(PLANK, 201), ({**PLANK, "notes": "x"}, 422)])
def test_a_duplicate_racing_past_the_check_is_caught_by_the_transaction(
        api, repo, monkeypatch, second_body, expected):
    """Two copies of one request both pass the fast-path lookup. The REQ# put
    inside the transaction is what stops the second one writing."""
    client = api()
    key = _key()
    first = _add(client, key)

    real = repo.get_request
    calls = {"n": 0}

    def blind_first_lookup(sub, k):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(sub, k)

    monkeypatch.setattr(repo, "get_request", blind_first_lookup)
    response = _add(client, key, second_body)

    assert response.status_code == expected
    if expected == 201:
        assert response.json() == first.json()
    assert list(repo.get_day("user-1", DATE)["exercises"]) == ["01"]


# ------------------------------------------------------ GET /v1/requests/{key}

def test_the_app_can_ask_whether_a_request_landed(api):
    client = api()
    key = _key()
    assert client.get(f"/v1/requests/{key}").status_code == 404
    _add(client, key)
    assert client.get(f"/v1/requests/{key}").json() == {"state": "done"}


def test_request_lookup_stays_in_the_callers_partition(api):
    key = _key()
    _add(api("user-1"), key)
    assert api("user-2").get(f"/v1/requests/{key}").status_code == 404


@pytest.mark.parametrize("key", ["x" * 36, "short"])
def test_request_lookup_validates_the_key(api, key):
    assert api().get(f"/v1/requests/{key}").status_code in (400, 422)
