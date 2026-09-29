"""Adding, replacing, removing and moving exercises."""

from __future__ import annotations

ROW = {"exercise": "Seated row", "unit": "lb",
       "sets": [{"reps": 10, "repsMax": 12, "weight": 40}]}


def test_add_assigns_order_logged_at_and_fills_the_catalog_gaps(api):
    day = api().post("/v1/days/2026-09-25/exercises", json=ROW).json()["day"]
    entry = day["exercises"]["01"]
    assert entry["order"] == 1
    assert entry["group"] == "Back"                       # filled from the lookup
    assert entry["muscles"] == ["Mid back", "Lats", "Biceps"]
    assert entry["loggedAt"].endswith("Z")
    assert day["version"] == 1


def test_client_cannot_set_order_or_logged_at(api):
    response = api().post("/v1/days/2026-09-25/exercises",
                          json={**ROW, "order": 7, "loggedAt": "1999-01-01T00:00:00Z"})
    assert response.status_code == 422


def test_order_is_highest_plus_one_even_after_a_removal(api):
    client = api()
    for name in ("Seated row", "Plank", "Bicep curl"):
        client.post("/v1/days/2026-09-25/exercises",
                    json={"exercise": name, "sets": [{"reps": 10}]})
    client.delete("/v1/days/2026-09-25/exercises/02")
    day = client.post("/v1/days/2026-09-25/exercises",
                      json={"exercise": "Plank", "sets": [{"seconds": 30}]}).json()["day"]
    assert sorted(day["exercises"]) == ["01", "03", "04"]
    assert day["exercises"]["04"]["order"] == 4


def test_put_replaces_every_field_but_keeps_order_and_logged_at(api):
    client = api()
    client.post("/v1/days/2026-09-25/exercises",
                json={**ROW, "notes": "old note", "perHand": True})
    before = client.get("/v1/days/2026-09-25").json()["day"]["exercises"]["01"]

    day = client.put("/v1/days/2026-09-25/exercises/01", json={
        "exercise": "Seated row", "unit": "kg", "sets": [{"reps": 8, "weight": 20}]}).json()["day"]
    after = day["exercises"]["01"]
    assert after["order"] == before["order"]
    assert after["loggedAt"] == before["loggedAt"]
    assert after["unit"] == "kg"
    assert "notes" not in after and "perHand" not in after   # no stale values left


def test_put_on_a_missing_exercise_is_404(api):
    client = api()
    client.post("/v1/days/2026-09-25/exercises", json=ROW)
    assert client.put("/v1/days/2026-09-25/exercises/09", json=ROW).status_code == 404


def test_delete_removes_the_exercise_and_then_the_day(api):
    client = api()
    client.post("/v1/days/2026-09-25/exercises", json=ROW)
    client.post("/v1/days/2026-09-25/exercises",
                json={"exercise": "Plank", "sets": [{"seconds": 30}]})

    day = client.delete("/v1/days/2026-09-25/exercises/01").json()["day"]
    assert list(day["exercises"]) == ["02"]

    body = client.delete("/v1/days/2026-09-25/exercises/02").json()
    assert body["day"] is None                       # last one gone, day removed
    assert client.get("/v1/days/2026-09-25").status_code == 404


def test_a_day_with_a_summary_survives_losing_its_last_exercise(api):
    client = api()
    client.post("/v1/days/2026-09-25/exercises", json=ROW)
    client.patch("/v1/days/2026-09-25", json={"summary": "Short back session."})
    day = client.delete("/v1/days/2026-09-25/exercises/01").json()["day"]
    assert day is not None and day["summary"] == "Short back session."
    assert day["exercises"] == {}


def test_move_is_one_transaction(api):
    client = api()
    client.post("/v1/days/2026-09-25/exercises", json=ROW)
    client.post("/v1/days/2026-09-21/exercises",
                json={"exercise": "Plank", "sets": [{"seconds": 30}]})

    target = client.post("/v1/days/2026-09-25/exercises/01/move",
                         json={"toDate": "2026-09-21"}).json()["day"]
    assert target["date"] == "2026-09-21"
    assert [e["exercise"] for e in target["exercises"].values()] == ["Plank", "Seated row"]
    assert target["exercises"]["02"]["order"] == 2
    assert client.get("/v1/days/2026-09-25").status_code == 404   # source day emptied


def test_move_to_an_empty_date_creates_it(api):
    client = api()
    client.post("/v1/days/2026-09-25/exercises", json=ROW)
    day = client.post("/v1/days/2026-09-25/exercises/01/move",
                      json={"toDate": "2026-09-26"}).json()["day"]
    assert day["date"] == "2026-09-26" and day["exercises"]["01"]["order"] == 1


def test_move_to_the_same_date_is_rejected(api):
    client = api()
    client.post("/v1/days/2026-09-25/exercises", json=ROW)
    response = client.post("/v1/days/2026-09-25/exercises/01/move",
                           json={"toDate": "2026-09-25"})
    assert response.status_code == 400


def test_move_a_missing_exercise_is_404(api):
    response = api().post("/v1/days/2026-09-25/exercises/01/move",
                          json={"toDate": "2026-09-26"})
    assert response.status_code == 404


def test_exercise_validation_messages(api):
    client = api()
    bad = [
        {"exercise": "", "sets": [{"reps": 10}]},
        {"exercise": "Row", "sets": []},
        {"exercise": "Row", "sets": [{}]},
        {"exercise": "Row", "sets": [{"reps": 10, "repsMax": 8}]},
        {"exercise": "Row", "sets": [{"reps": 10}], "muscles": ["Quadzilla"]},
        {"exercise": "Row", "sets": [{"reps": 10}], "group": "Cardiovascular"},
        {"exercise": "x" * 81, "sets": [{"reps": 10}]},
        {"exercise": "Row", "sets": [{"reps": 10}] * 51},
        {"exercise": "Row", "sets": [{"weight": 5000}]},
    ]
    for body in bad:
        response = client.post("/v1/days/2026-09-25/exercises", json=body)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "validation_error"


def test_unknown_muscle_message_lists_the_valid_ones(api):
    response = api().post("/v1/days/2026-09-25/exercises",
                          json={"exercise": "Row", "sets": [{"reps": 10}], "muscles": ["Quadzilla"]})
    message = response.json()["error"]["message"]
    assert "Quadzilla" in message and "Hamstrings" in message


def test_muscle_names_are_case_insensitive(api):
    day = api().post("/v1/days/2026-09-25/exercises", json={
        "exercise": "Treadmill walk", "muscles": ["cardio", "CALVES", "Glutes"],
        "sets": [{"minutes": 20}]}).json()["day"]
    assert day["exercises"]["01"]["muscles"] == ["Cardio", "Calves", "Glutes"]


def test_a_day_holds_at_most_99_exercises(api, repo):
    exercises = {f"{n:02d}": {"order": n, "exercise": "Plank", "group": "Core",
                              "muscles": ["Core"], "unit": "lb",
                              "sets": [{"seconds": 30}], "loggedAt": "2026-09-25T00:00:00Z"}
                 for n in range(1, 100)}
    repo.mutate_day("user-1", "2026-09-25",
                    lambda _d: {"date": "2026-09-25", "exercises": exercises})
    response = api().post("/v1/days/2026-09-25/exercises",
                          json={"exercise": "Plank", "sets": [{"seconds": 30}]})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "day_full"
