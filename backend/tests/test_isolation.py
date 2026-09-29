"""A second user must not see, change or even learn about the first user's log.
The sub comes from the JWT, so there is no request field to tamper with."""

from __future__ import annotations

ROW = {"exercise": "Seated row", "unit": "lb", "sets": [{"reps": 10, "weight": 40}]}


def test_a_second_user_sees_an_empty_log(api):
    api("user-1").post("/v1/days/2026-09-25/exercises", json=ROW)
    body = api("user-2", "other@example.com").get("/v1/days").json()
    assert body["days"] == [] and body["dataVersion"] == 0


def test_a_second_user_cannot_read_or_change_the_first_users_day(api):
    api("user-1").post("/v1/days/2026-09-25/exercises", json=ROW)
    intruder = api("user-2", "other@example.com")

    assert intruder.get("/v1/days/2026-09-25").status_code == 404
    assert intruder.put("/v1/days/2026-09-25/exercises/01", json=ROW).status_code == 404
    assert intruder.delete("/v1/days/2026-09-25/exercises/01").status_code == 404
    assert intruder.post("/v1/days/2026-09-25/exercises/01/move",
                         json={"toDate": "2026-09-26"}).status_code == 404

    # A PATCH creates the intruder's own day; it must not touch user-1's.
    intruder.patch("/v1/days/2026-09-25", json={"notes": "not mine"})
    owner_day = api("user-1").get("/v1/days/2026-09-25").json()["day"]
    assert "notes" not in owner_day
    assert owner_day["exercises"]["01"]["exercise"] == "Seated row"


def test_each_user_has_their_own_data_version(api):
    one, two = api("user-1"), api("user-2", "other@example.com")
    one.post("/v1/days/2026-09-25/exercises", json=ROW)
    one.post("/v1/days/2026-09-24/exercises", json=ROW)
    assert one.get("/v1/days").json()["dataVersion"] == 2
    assert two.get("/v1/days").json()["dataVersion"] == 0
