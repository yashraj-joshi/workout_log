"""POST /v1/assistant, end to end with the model scripted.

OpenAI is never reached. What the model *decides* is scripted; what happens
next - the tools, service.py, the Pydantic models, the conditional writes -
is the real code, so these tests cover the path a voice command actually
takes. The cases are the ones the brief lists.
"""

from __future__ import annotations

import base64

import pytest

from conftest import scripted

TODAY = "2026-09-25"
AUDIO = base64.b64encode(b"not really audio").decode()


def turn(client, said="seated row, 3 sets of 10 to 12 at 40 pounds", date=TODAY, today=TODAY):
    return client.post("/v1/assistant", json={"text": said, "date": date, "today": today,
                                              "timezone": "America/New_York"})


# --------------------------------------------------------------------- logging

def test_logging_a_new_exercise(assistant):
    script = [("add_exercise", {
        "date": TODAY, "exercise": "Seated row", "group": "Back",
        "muscles": ["Mid back", "Lats", "Biceps"], "unit": "lb", "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": 12, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}] * 3,
    })]
    client = assistant(script=script, reply="Logged #1 Seated row: 3 × 10–12 @ 40 lb.")
    body = turn(client).json()

    assert body["reply"].startswith("Logged #1 Seated row")
    assert body["changedDates"] == [TODAY]
    assert body["undoToken"]
    [day] = body["days"]
    assert day["exercises"]["01"]["exercise"] == "Seated row"
    assert day["exercises"]["01"]["sets"][0] == {"reps": 10, "repsMax": 12, "weight": 40}
    # The muscles came from the model; the area and order from the server.
    assert day["exercises"]["01"]["order"] == 1
    assert day["exercises"]["01"]["group"] == "Back"


def test_adding_sets_appends_rather_than_replacing(assistant, repo):
    client = assistant(script=[("add_exercise", {
        "date": TODAY, "exercise": "Seated row", "group": None, "muscles": None, "unit": "lb",
        "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    turn(client)

    client = assistant(script=[("add_sets", {
        "date": TODAY, "key": "01",
        "sets": [{"reps": 8, "repsMax": None, "weight": 45, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })], reply="Added a set to #1 Seated row.")
    body = turn(client, "one more set, 8 at 45").json()

    sets = body["days"][0]["exercises"]["01"]["sets"]
    assert sets == [{"reps": 10, "weight": 40}, {"reps": 8, "weight": 45}], "the first set survived"


def test_add_sets_on_an_exercise_that_is_not_there_tells_the_model_why(assistant):
    agent = scripted(("add_sets", {"date": TODAY, "key": "07", "sets": [{"reps": 8}]}),
                     reply="I couldn't find that one.")
    client = assistant(script=[], agent=agent)
    response = turn(client, "add a set to the seventh one")

    assert response.status_code == 200, "a tool error is the model's to handle, not a 500"
    assert "no exercise 07" in agent.results[0]["error"]


def test_the_25s_ambiguity_is_logged_one_way_and_said_out_loud(assistant):
    """"Dumbbell press, 3 sets of 10 with the 25s" - 25 lb dumbbells, most
    likely, and the assumption comes back so it can be corrected."""
    client = assistant(
        script=[("add_exercise", {
            "date": TODAY, "exercise": "Dumbbell bench press", "group": "Chest",
            "muscles": ["Chest", "Triceps"], "unit": "lb", "perHand": True, "notes": None,
            "sets": [{"reps": 10, "repsMax": None, "weight": 25, "seconds": None, "minutes": None,
                      "distance": None, "distanceUnit": None, "note": None}] * 3,
        })],
        reply="Logged #1 Dumbbell bench press: 3 × 10 @ 25 lb.",
        assumptions=["Took \"the 25s\" as 25 lb dumbbells, per hand."])
    body = turn(client, "dumbbell press, 3 sets of 10 with the 25s").json()

    assert body["assumptions"] == ['Took "the 25s" as 25 lb dumbbells, per hand.']
    assert body["days"][0]["exercises"]["01"]["perHand"] is True


def test_missing_reps_logs_what_there_is_and_asks_once(assistant):
    client = assistant(
        script=[("add_exercise", {
            "date": TODAY, "exercise": "Leg press", "group": "Legs", "muscles": ["Quads", "Glutes"],
            "unit": "lb", "perHand": None, "notes": None,
            "sets": [{"reps": None, "repsMax": None, "weight": 120, "seconds": None, "minutes": None,
                      "distance": None, "distanceUnit": None, "note": None}] * 3,
        })],
        reply="Logged #1 Leg press: 3 sets @ 120 lb.",
        question="How many reps on the leg press?")
    body = turn(client, "leg press, three sets at 120").json()

    assert body["question"] == "How many reps on the leg press?"
    sets = body["days"][0]["exercises"]["01"]["sets"]
    assert sets == [{"weight": 120}] * 3, "logged without inventing reps"


def test_a_correction_rewrites_the_exercise(assistant):
    client = assistant(script=[("add_exercise", {
        "date": TODAY, "exercise": "Seated row", "group": None, "muscles": None, "unit": "lb",
        "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    turn(client)

    client = assistant(script=[("replace_exercise", {
        "date": TODAY, "key": "01", "exercise": "Seated row", "group": "Back",
        "muscles": ["Mid back", "Lats", "Biceps"], "unit": "lb", "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 50, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })], reply="Changed #1 Seated row to 10 @ 50 lb.")
    body = turn(client, "that row was 50, not 40").json()

    exercise = body["days"][0]["exercises"]["01"]
    assert exercise["sets"] == [{"reps": 10, "weight": 50}]
    assert exercise["order"] == 1, "a correction keeps its place in the day"


def test_a_removal_takes_the_exercise_out(assistant):
    client = assistant(script=[("add_exercise", {
        "date": TODAY, "exercise": "Plank", "group": "Core", "muscles": ["Core"], "unit": "lb",
        "perHand": None, "notes": None,
        "sets": [{"reps": None, "repsMax": None, "weight": None, "seconds": 45, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    turn(client)

    client = assistant(script=[("remove_exercise", {"date": TODAY, "key": "01"})],
                       reply="Removed #1 Plank.")
    body = turn(client, "scratch the plank").json()

    # The day had nothing else and no summary or notes, so it went with it.
    assert body["days"][0]["deleted"] is True


def test_place_and_notes_go_where_they_belong(assistant):
    client = assistant(script=[
        ("add_exercise", {
            "date": TODAY, "exercise": "Goblet squat", "group": "Legs",
            "muscles": ["Quads", "Glutes"], "unit": "lb", "perHand": None, "notes": None,
            "sets": [{"reps": 10, "repsMax": None, "weight": 35, "seconds": None, "minutes": None,
                      "distance": None, "distanceUnit": None, "note": None}],
        }),
        ("update_day", {"date": TODAY, "place": "gym", "notes": "Back felt fine.",
                        "notesMode": None, "bodyweight": None}),
    ], reply="Logged #1 Goblet squat and marked it a gym day.")
    day = turn(client, "at the gym, goblet squat 10 at 35, back felt fine").json()["days"][0]

    assert day["place"] == "gym"
    assert day["notes"] == "Back felt fine."


def test_notes_append_rather_than_overwrite(assistant):
    client = assistant(script=[("update_day", {
        "date": TODAY, "place": None, "notes": "Back felt fine.", "notesMode": None,
        "bodyweight": None})])
    turn(client, "back felt fine")

    client = assistant(script=[("update_day", {
        "date": TODAY, "place": None, "notes": "Shoulder a bit sore.", "notesMode": None,
        "bodyweight": None})])
    day = turn(client, "shoulder a bit sore").json()["days"][0]

    assert day["notes"] == "Back felt fine. Shoulder a bit sore."


def test_notes_can_be_replaced_outright_when_asked(assistant):
    client = assistant(script=[("update_day", {
        "date": TODAY, "place": None, "notes": "Back felt fine.", "notesMode": None,
        "bodyweight": None})])
    turn(client)

    client = assistant(script=[("update_day", {
        "date": TODAY, "place": None, "notes": "Actually my back was sore.",
        "notesMode": "replace", "bodyweight": None})])
    day = turn(client, "no, my back was sore").json()["days"][0]

    assert day["notes"] == "Actually my back was sore."


def test_bodyweight_goes_in_its_own_field(assistant):
    client = assistant(script=[("update_day", {
        "date": TODAY, "place": None, "notes": None, "notesMode": None, "bodyweight": 180.4})],
        reply="Noted 180.4 lb.")
    day = turn(client, "I weighed 180.4 this morning").json()["days"][0]

    assert day["bodyweight"] == 180.4
    assert "notes" not in day


# ------------------------------------------------------------ done for today

def test_done_for_today_by_voice_writes_the_summary_once(assistant, repo):
    client = assistant(script=[("add_exercise", {
        "date": TODAY, "exercise": "Seated row", "group": None, "muscles": None, "unit": "lb",
        "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    turn(client)

    first = scripted(("finish_day", {"date": TODAY, "notes": None}), reply="Summary written.")
    body = turn(assistant(script=[], agent=first), "done for today").json()
    assert first.results[0]["ok"] is True
    assert body["days"][0]["summary"]
    assert body["days"][0]["summaryGeneratedAt"]

    # Saying it again costs no model call and tells the model why.
    second = scripted(("finish_day", {"date": TODAY, "notes": None}),
                      reply="Today's summary is already written. You can edit it in the summary box.")
    client = assistant(script=[], agent=second)
    assert turn(client, "done for today").status_code == 200
    assert second.results[0]["code"] == "already_summarized"
    assert len(assistant.calls) == 1, "the model wrote one summary, not two"


def test_finish_day_refuses_a_day_with_nothing_on_it(assistant):
    agent = scripted(("finish_day", {"date": TODAY, "notes": None}), reply="Nothing to summarise.")
    client = assistant(script=[], agent=agent)
    turn(client, "done for today")
    assert agent.results[0]["code"] == "no_exercises"


# ------------------------------------------------------------------ questions

def test_a_question_with_no_data_says_so(assistant):
    agent = scripted(("get_exercise_history", {"name": "bench press"}),
                     reply="Nothing logged for bench press yet.")
    client = assistant(script=[], agent=agent)
    body = turn(client, "how's my bench going?").json()

    assert agent.results[0] == {"exercise": "bench press", "count": 0, "sessions": []}
    assert body["changedDates"] == [], "a question changes nothing"
    assert body["undoToken"] is None


def test_a_question_reads_the_days_back(assistant):
    client = assistant(script=[("add_exercise", {
        "date": "2026-09-21", "exercise": "Seated row", "group": None, "muscles": None,
        "unit": "lb", "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    turn(client, date="2026-09-21")

    agent = scripted(("get_days", {"from": "2026-09-20", "to": "2026-09-26"}),
                     reply="On Sep 21 you did seated row, 10 @ 40 lb.")
    turn(assistant(script=[], agent=agent), "what did I do Monday?")

    result = agent.results[0]
    assert result["count"] == 1
    assert result["days"][0]["exercises"][0]["sets"] == "10 @ 40 lb"


def test_get_days_puts_the_range_the_right_way_round(assistant):
    agent = scripted(("get_days", {"from": "2026-09-26", "to": "2026-09-20"}), reply="ok")
    turn(assistant(script=[], agent=agent))
    assert agent.results[0]["from"] == "2026-09-20"
    assert agent.results[0]["to"] == "2026-09-26"


# ---------------------------------------------------------------------- undo

def test_undo_puts_the_day_back(assistant):
    client = assistant(script=[("add_exercise", {
        "date": TODAY, "exercise": "Seated row", "group": None, "muscles": None, "unit": "lb",
        "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    token = turn(client).json()["undoToken"]

    undone = client.post("/v1/assistant/undo", json={"undoToken": token})
    assert undone.status_code == 200
    assert undone.json()["days"][0]["deleted"] is True, "the day was new, so it goes entirely"

    # The token is spent.
    again = client.post("/v1/assistant/undo", json={"undoToken": token})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_undone"


def test_undo_restores_what_was_there_before(assistant):
    client = assistant(script=[("add_exercise", {
        "date": TODAY, "exercise": "Seated row", "group": None, "muscles": None, "unit": "lb",
        "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    turn(client)

    client = assistant(script=[("remove_exercise", {"date": TODAY, "key": "01"})])
    token = turn(client, "remove that").json()["undoToken"]

    day = client.post("/v1/assistant/undo", json={"undoToken": token}).json()["days"][0]
    assert day["exercises"]["01"]["exercise"] == "Seated row"


def test_undo_refuses_when_the_day_moved_on(assistant, api):
    client = assistant(script=[("add_exercise", {
        "date": TODAY, "exercise": "Seated row", "group": None, "muscles": None, "unit": "lb",
        "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    token = turn(client).json()["undoToken"]

    # An edit from the app, after the turn.
    api().patch(f"/v1/days/{TODAY}", json={"notes": "felt good"})

    refused = client.post("/v1/assistant/undo", json={"undoToken": token})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "day_changed"


def test_undo_never_takes_the_summary_back(assistant):
    client = assistant(script=[("add_exercise", {
        "date": TODAY, "exercise": "Seated row", "group": None, "muscles": None, "unit": "lb",
        "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    turn(client)
    client = assistant(script=[("finish_day", {"date": TODAY, "notes": None})])
    token = turn(client, "done for today").json()["undoToken"]

    day = client.post("/v1/assistant/undo", json={"undoToken": token}).json()["days"][0]
    assert day["summaryGeneratedAt"], "undoing must not hand back a second free summary"


def test_an_unknown_undo_token_is_a_404(assistant):
    client = assistant(script=[])
    response = client.post("/v1/assistant/undo", json={"undoToken": "2026-01-01T00:00:00Z#nope"})
    assert response.status_code == 404


# ------------------------------------------------------- permissions and limits

def test_a_user_without_ai_gets_403_on_both_routes(assistant):
    client = assistant(groups=("users",), script=[])
    assert turn(client).status_code == 403
    assert turn(client).json()["error"]["code"] == "ai_not_enabled"
    assert client.post("/v1/assistant/undo", json={"undoToken": "x#y"}).status_code == 403


def test_the_daily_limit_stops_at_the_cap(assistant, monkeypatch):
    monkeypatch.setenv("DAILY_AI_LIMIT", "2")
    client = assistant(script=[])
    assert turn(client).status_code == 200
    assert turn(client).status_code == 200
    third = turn(client)
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "daily_limit"
    assert "resets at midnight UTC" in third.json()["error"]["message"]


def test_a_failed_turn_does_not_spend_one_of_the_day_s_calls(assistant, monkeypatch):
    from workoutlog.errors import ApiError

    monkeypatch.setenv("DAILY_AI_LIMIT", "1")

    def broken(**kwargs):
        raise ApiError(502, "ai_unavailable", "Couldn't reach the AI. Nothing was logged.")

    assert turn(assistant(script=[], agent=broken)).status_code == 502
    # The counter was given back, so the one call for the day is still there.
    assert turn(assistant(script=[])).status_code == 200


def test_one_user_cannot_touch_another_s_log(assistant):
    mine = assistant(script=[("add_exercise", {
        "date": TODAY, "exercise": "Seated row", "group": None, "muscles": None, "unit": "lb",
        "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    token = turn(mine).json()["undoToken"]

    theirs = assistant(sub="user-2", email="someone@example.com", script=[])
    assert theirs.post("/v1/assistant/undo", json={"undoToken": token}).status_code == 404

    agent = scripted(("get_days", {"from": "2026-01-01", "to": "2026-12-31"}), reply="ok")
    turn(assistant(sub="user-2", email="someone@example.com", script=[], agent=agent))
    assert agent.results[0]["count"] == 0, "a second user's partition is empty"


# ------------------------------------------------------------------ the input

def test_speech_becomes_the_transcript(assistant):
    client = assistant(script=[], transcript="seated row three sets of ten at forty",
                       reply="Logged it.")
    response = client.post("/v1/assistant", json={
        "audioBase64": AUDIO, "audioMimeType": "audio/mp4",
        "date": TODAY, "today": TODAY, "timezone": "America/New_York"})

    assert response.status_code == 200
    assert response.json()["transcript"] == "seated row three sets of ten at forty"


def test_silence_is_not_sent_to_the_model(assistant):
    client = assistant(script=[], transcript="   ")
    response = client.post("/v1/assistant", json={
        "audioBase64": AUDIO, "audioMimeType": "audio/mp4",
        "date": TODAY, "today": TODAY})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "no_speech"


def test_a_recording_over_the_size_limit_is_refused(assistant):
    client = assistant(script=[])
    big = base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode()
    response = client.post("/v1/assistant", json={
        "audioBase64": big, "audioMimeType": "audio/webm", "date": TODAY, "today": TODAY})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "audio_too_large"


@pytest.mark.parametrize("body", [
    {"date": TODAY, "today": TODAY},                                   # neither
    {"text": "hi", "audioBase64": AUDIO, "audioMimeType": "audio/mp4",
     "date": TODAY, "today": TODAY},                                   # both
    {"audioBase64": AUDIO, "audioMimeType": "audio/ogg", "date": TODAY, "today": TODAY},
    {"text": "x" * 501, "date": TODAY, "today": TODAY},
    {"text": "hi", "date": "not-a-date", "today": TODAY},
])
def test_a_request_that_makes_no_sense_is_refused(assistant, body):
    assert assistant(script=[]).post("/v1/assistant", json=body).status_code in (400, 422)


def test_the_model_is_told_what_is_already_on_the_day(assistant):
    client = assistant(script=[("add_exercise", {
        "date": TODAY, "exercise": "Seated row", "group": None, "muscles": None, "unit": "lb",
        "perHand": None, "notes": None,
        "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None, "minutes": None,
                  "distance": None, "distanceUnit": None, "note": None}],
    })])
    turn(client)

    agent = scripted(reply="ok")
    turn(assistant(script=[], agent=agent), "what's on today?")

    assert "#1 key=01 Seated row" in agent.context
    assert "10 @ 40 lb" in agent.context
    assert "Timezone: America/New_York" in agent.context
    assert "Seated row" in agent.context.split("names he already uses:")[1]
