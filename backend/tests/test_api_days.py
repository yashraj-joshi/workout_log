"""GET/PATCH on days, the ETag path, and validation."""

from __future__ import annotations

import base64
import json

import pytest


def test_health_needs_no_auth(api):
    body = api().get("/health").json()
    assert body["ok"] is True and "commit" in body


def test_me_reports_ai_access(api):
    assert api(groups=("ai-users",)).get("/v1/me").json() == {
        "email": "yash@example.com", "groups": ["ai-users"], "canUseAI": True}
    assert api(groups=()).get("/v1/me").json()["canUseAI"] is False
    assert api(groups=("admins",)).get("/v1/me").json()["canUseAI"] is True


def test_days_list_is_newest_first(seeded):
    body = seeded.get("/v1/days").json()
    assert [d["date"] for d in body["days"]] == ["2026-09-25", "2026-09-21"]
    assert body["nextCursor"] is None


def test_etag_returns_304_when_nothing_changed(seeded):
    first = seeded.get("/v1/days")
    etag = first.headers["ETag"]
    assert first.status_code == 200

    again = seeded.get("/v1/days", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["ETag"] == etag

    # Any write moves dataVersion, so the same ETag stops matching.
    seeded.patch("/v1/days/2026-09-25", json={"place": "gym"})
    after = seeded.get("/v1/days", headers={"If-None-Match": etag})
    assert after.status_code == 200
    assert after.headers["ETag"] != etag


def test_etag_accepts_weak_and_multiple_values(seeded):
    etag = seeded.get("/v1/days").headers["ETag"]
    weak = seeded.get("/v1/days", headers={"If-None-Match": f'W/{etag}'})
    assert weak.status_code == 304
    listed = seeded.get("/v1/days", headers={"If-None-Match": f'"999", {etag}'})
    assert listed.status_code == 304


def test_paging_returns_a_cursor(api):
    client = api()
    for day in range(1, 6):
        client.post(f"/v1/days/2026-09-0{day}/exercises",
                    json={"exercise": "Plank", "sets": [{"seconds": 30}]})
    first = client.get("/v1/days", params={"limit": 2}).json()
    assert [d["date"] for d in first["days"]] == ["2026-09-05", "2026-09-04"]
    assert first["nextCursor"]
    second = client.get("/v1/days", params={"limit": 2, "cursor": first["nextCursor"]}).json()
    assert [d["date"] for d in second["days"]] == ["2026-09-03", "2026-09-02"]


def test_bad_cursor_is_rejected(api):
    response = api().get("/v1/days", params={"cursor": "not-base64!!"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_cursor"


@pytest.mark.parametrize("start", [
    {"PK": "USER#user-2", "SK": "DAY#2026-09-01"},      # someone else's partition
    {"PK": "USER#user-1", "SK": "PROFILE"},             # not a day item
    {"PK": "USER#user-1"},                              # wrong shape
    ["USER#user-1", "DAY#2026-09-01"],                  # not an object
])
def test_a_crafted_cursor_is_400_not_500(api, start):
    cursor = base64.urlsafe_b64encode(json.dumps(start).encode()).decode().rstrip("=")
    response = api().get("/v1/days", params={"cursor": cursor})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_cursor"


def test_get_missing_day_is_404(api):
    response = api().get("/v1/days/2026-01-01")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_patch_creates_the_day(api):
    client = api()
    day = client.patch("/v1/days/2026-09-25", json={"notes": "Back felt fine."}).json()["day"]
    assert day["notes"] == "Back felt fine."
    assert day["exercises"] == {}
    assert day["version"] == 1


def test_patch_null_removes_a_field_and_absent_leaves_it(api):
    client = api()
    client.patch("/v1/days/2026-09-25", json={"notes": "sore", "bodyweight": 180})
    day = client.patch("/v1/days/2026-09-25", json={"notes": None}).json()["day"]
    assert "notes" not in day
    assert day["bodyweight"] == 180          # untouched because it was absent


def test_editing_the_summary_keeps_summary_generated_at(api, repo):
    client = api()
    client.post("/v1/days/2026-09-25/exercises",
                json={"exercise": "Plank", "sets": [{"seconds": 30}]})
    repo.set_summary_once("user-1", "2026-09-25", "Generated text.")
    stamp = repo.get_day("user-1", "2026-09-25")["summaryGeneratedAt"]

    day = client.patch("/v1/days/2026-09-25", json={"summary": "My own words."}).json()["day"]
    assert day["summary"] == "My own words."
    assert day["summaryGeneratedAt"] == stamp


def test_empty_patch_body_is_rejected(api):
    response = api().patch("/v1/days/2026-09-25", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_validation_errors_use_the_error_envelope(api):
    client = api()
    cases = [
        ("/v1/days/2026-13-40", {"place": "gym"}, "validation_error"),
        ("/v1/days/2026-09-25", {"place": "beach"}, "validation_error"),
        ("/v1/days/2026-09-25", {"summary": "x" * 601}, "validation_error"),
        ("/v1/days/2026-09-25", {"bodyweight": 5}, "validation_error"),
        ("/v1/days/2026-09-25", {"nope": 1}, "validation_error"),
    ]
    for path, body, code in cases:
        response = client.patch(path, json=body)
        assert response.status_code in (400, 422), (path, body)
        payload = response.json()["error"]
        assert payload["code"] == code and payload["message"], (path, body)


def test_a_day_left_with_nothing_is_deleted(api):
    client = api()
    client.patch("/v1/days/2026-09-25", json={"notes": "sore"})
    assert client.get("/v1/days/2026-09-25").status_code == 200
    body = client.patch("/v1/days/2026-09-25", json={"notes": None}).json()
    assert body["day"] is None
    assert client.get("/v1/days/2026-09-25").status_code == 404
