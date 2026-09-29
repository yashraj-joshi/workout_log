"""POST /v1/days/{date}/finish - "Done for today".

The rule under test: one generated summary per day, ever. Editing afterwards
is a PATCH and must never revive the button.
"""

from __future__ import annotations

import pytest

DATE = "2026-09-25"


def _log(client):
    client.post(f"/v1/days/{DATE}/exercises",
                json={"exercise": "Treadmill walk", "sets": [{"minutes": 19.8, "distance": 1.07}]})
    client.post(f"/v1/days/{DATE}/exercises", json={
        "exercise": "Suitcase carry", "unit": "lb",
        "muscles": ["Core", "Lower back", "Side glutes"],
        "sets": [{"reps": 5, "weight": 20, "note": "each side"}]})


def test_finish_writes_the_summary_once(api, assistant):
    _log(api())
    client = assistant()

    first = client.post(f"/v1/days/{DATE}/finish")
    assert first.status_code == 200
    day = first.json()["day"]
    assert day["summary"] == "Walked and carried. 2 exercises, 2 sets."
    assert day["summaryGeneratedAt"]

    second = client.post(f"/v1/days/{DATE}/finish")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_summarized"
    assert "already written" in second.json()["error"]["message"]
    assert len(assistant.calls) == 1, "the model must not be called a second time"


def test_the_model_only_sees_facts_the_code_computed(api, assistant):
    api().post("/v1/days/2026-09-21/exercises",
               json={"exercise": "Treadmill walk", "sets": [{"minutes": 25, "distance": 1.3}]})
    _log(api())
    assistant().post(f"/v1/days/{DATE}/finish")

    facts = assistant.calls[0]
    assert "Totals: 2 exercises, 2 sets, 5 reps" in facts
    assert "Cardio: 19.8 min, 1.1 mi" in facts
    assert "Sets per muscle: Core 1, Lower back 1, Side glutes 1" in facts
    assert "No direct work: Chest, Back, Shoulders" in facts
    # The comparison with the previous treadmill session is computed, not guessed.
    assert "last time 2026-09-21 was 25 min (−5.2 min)" in facts
    assert "Suitcase carry" in facts and "first time logged" in facts


def test_a_simultaneous_second_call_cannot_write_a_second_summary(api, assistant, repo):
    """The other request finishes while our model call is still in flight, so
    our conditional write is the one that loses."""
    _log(api())

    def racing(_facts: str) -> str:
        repo.set_summary_once("user-1", DATE, "The other request got there first.")
        return "Ours."

    response = assistant(summarizer=racing).post(f"/v1/days/{DATE}/finish")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_summarized"
    assert repo.get_day("user-1", DATE)["summary"] == "The other request got there first."


def test_finish_on_an_empty_day_is_400(api, assistant):
    api().patch(f"/v1/days/{DATE}", json={"notes": "rest day"})
    for client in (assistant(), assistant()):
        response = client.post("/v1/days/2026-01-01/finish")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "no_exercises"
    assert assistant.calls == []


def test_finish_saves_notes_passed_with_it(api, assistant):
    _log(api())
    day = assistant().post(f"/v1/days/{DATE}/finish",
                           json={"notes": "Back felt fine."}).json()["day"]
    assert day["notes"] == "Back felt fine."
    assert "How it felt: Back felt fine." in assistant.calls[0]


def test_a_user_without_ai_access_gets_403(api, assistant):
    _log(api())
    response = assistant("user-1", groups=("readers",)).post(f"/v1/days/{DATE}/finish")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ai_not_enabled"
    assert assistant.calls == []


def test_the_daily_limit_returns_429(api, assistant, monkeypatch):
    monkeypatch.setenv("DAILY_AI_LIMIT", "2")
    client = assistant()
    for date in ("2026-09-21", "2026-09-22", "2026-09-23"):
        api().post(f"/v1/days/{date}/exercises",
                   json={"exercise": "Plank", "sets": [{"seconds": 30}]})

    assert client.post("/v1/days/2026-09-21/finish").status_code == 200
    assert client.post("/v1/days/2026-09-22/finish").status_code == 200

    blocked = client.post("/v1/days/2026-09-23/finish")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "daily_limit"
    assert "Daily AI limit of 2" in blocked.json()["error"]["message"]
    assert len(assistant.calls) == 2


def test_an_empty_model_reply_is_not_saved(api, assistant, repo):
    _log(api())
    response = assistant(summarizer=lambda _f: "   ").post(f"/v1/days/{DATE}/finish")
    assert response.status_code == 502
    assert "summaryGeneratedAt" not in repo.get_day("user-1", DATE)


def test_editing_a_generated_summary_does_not_reopen_the_day(api, assistant, repo):
    _log(api())
    assistant().post(f"/v1/days/{DATE}/finish")
    api().patch(f"/v1/days/{DATE}", json={"summary": "My own words."})

    day = repo.get_day("user-1", DATE)
    assert day["summary"] == "My own words." and day["summaryGeneratedAt"]
    assert assistant().post(f"/v1/days/{DATE}/finish").status_code == 409


@pytest.mark.parametrize("path", ["/v1/days/2026-13-01/finish", "/v1/days/nope/finish"])
def test_finish_validates_the_date(assistant, path):
    assert assistant().post(path).status_code in (400, 422)
