"""The assistant loop, with the Responses API faked.

The fakes are built from the SDK's own response types, so if the pinned SDK
changes the shape of a function call or of usage, these go red rather than
the deployed function. Nothing here reaches OpenAI.
"""

from __future__ import annotations

import json

import pytest
from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall

from workoutlog.assistant import agent, prompts, tools
from workoutlog.errors import ApiError


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    """What the SDK hands back: `output` items, `output_text` for the final
    message, and `usage`."""

    def __init__(self, *, calls=(), text="", usage=(100, 20), incomplete=None, status="completed"):
        self.output = [
            ResponseFunctionToolCall(type="function_call", call_id=f"call_{i}",
                                     name=name, arguments=json.dumps(args))
            for i, (name, args) in enumerate(calls)
        ]
        self.output_text = text
        self.usage = FakeUsage(*usage)
        self.incomplete_details = incomplete
        self.status = status


def final(reply="Logged it.", assumptions=(), question=None, **kw):
    return FakeResponse(text=json.dumps({
        "reply": reply, "assumptions": list(assumptions), "question": question}), **kw)


def scripted_create(*responses):
    """Returns a `create` that hands back each response in turn and records
    every call it was given."""
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        return responses[min(len(sent) - 1, len(responses) - 1)]

    create.sent = sent
    return create


@pytest.fixture
def session(repo):
    return tools.Session(repo, "user-1", "2026-09-25")


def run(session, create, said="seated row 3 of 10 at 40"):
    return agent.run(session=session, context="ctx", said=said, create=create)


def test_a_turn_with_no_tool_calls_comes_straight_back(session):
    create = scripted_create(final(reply="Nothing logged for bench press yet."))
    out = run(session, create)

    assert out["reply"] == "Nothing logged for bench press yet."
    assert out["modelCalls"] == 1
    assert session.changed == []


def test_a_tool_call_runs_and_the_result_goes_back_to_the_model(session):
    create = scripted_create(
        FakeResponse(calls=[("add_exercise", {
            "date": "2026-09-25", "exercise": "Seated row", "group": "Back",
            "muscles": ["Mid back"], "unit": "lb", "perHand": None, "notes": None,
            "sets": [{"reps": 10, "repsMax": None, "weight": 40, "seconds": None,
                      "minutes": None, "distance": None, "distanceUnit": None, "note": None}],
        })]),
        final(reply="Logged #1 Seated row: 10 @ 40 lb."),
    )
    out = run(session, create)

    assert out["modelCalls"] == 2
    assert session.changed == ["2026-09-25"]

    # The second call carries the first call's items and one output per call.
    second = create.sent[1]["input"]
    assert second[0]["role"] == "developer"
    assert second[1]["role"] == "user"
    outputs = [i for i in second if i.get("type") == "function_call_output"]
    assert len(outputs) == 1
    assert outputs[0]["call_id"] == "call_0"
    assert json.loads(outputs[0]["output"])["ok"] is True


def test_several_tool_calls_in_one_turn_each_get_an_output(session):
    create = scripted_create(
        FakeResponse(calls=[
            ("update_day", {"date": "2026-09-25", "place": "gym", "notes": None,
                            "notesMode": None, "bodyweight": None}),
            ("update_day", {"date": "2026-09-25", "place": None, "notes": None,
                            "notesMode": None, "bodyweight": 180}),
        ]),
        final(),
    )
    run(session, create)

    outputs = [i for i in create.sent[1]["input"] if i.get("type") == "function_call_output"]
    assert [o["call_id"] for o in outputs] == ["call_0", "call_1"]


def test_tokens_add_up_across_the_round_trips(session):
    create = scripted_create(
        FakeResponse(calls=[("get_exercise_history", {"name": "bench press"})], usage=(500, 30)),
        final(usage=(700, 50)),
    )
    out = run(session, create)

    assert out["inputTokens"] == 1200
    assert out["outputTokens"] == 80


def test_the_request_carries_the_tools_and_the_reply_schema(session):
    create = scripted_create(final())
    run(session, create)
    sent = create.sent[0]

    assert [t["name"] for t in sent["tools"]] == [s["name"] for s in tools.SPECS]
    assert all(t["strict"] is True for t in sent["tools"])
    assert sent["text"]["format"]["type"] == "json_schema"
    assert sent["text"]["format"]["strict"] is True
    assert sent["store"] is False, "the log must not be kept as state on OpenAI's side"
    assert sent["reasoning"] == {"effort": "low"}
    assert sent["timeout"] <= agent.DEADLINE_SECONDS


def test_a_model_that_never_stops_calling_tools_is_cut_off(session):
    forever = scripted_create(FakeResponse(calls=[("get_days", {"from": "2026-09-01", "to": "2026-09-30"})]))
    with pytest.raises(ApiError) as caught:
        run(session, forever)

    assert caught.value.status == 504
    assert len(forever.sent) == agent.MAX_MODEL_CALLS


def test_an_empty_answer_is_an_error_rather_than_a_blank_reply(session):
    class Incomplete:
        reason = "max_output_tokens"

    create = scripted_create(FakeResponse(text="", incomplete=Incomplete(), status="incomplete"))
    with pytest.raises(ApiError) as caught:
        run(session, create)

    assert caught.value.status == 502
    assert caught.value.code == "ai_unavailable"


def test_a_plain_sentence_instead_of_json_is_still_shown(session):
    create = scripted_create(FakeResponse(text="Logged it."))
    assert run(session, create)["reply"] == "Logged it."


def test_assumptions_and_a_question_come_through(session):
    create = scripted_create(final(reply="Logged it.", assumptions=["Took the 25s as 25 lb."],
                                   question="How many reps?"))
    out = run(session, create)

    assert out["assumptions"] == ["Took the 25s as 25 lb."]
    assert out["question"] == "How many reps?"


def test_an_empty_question_is_no_question(session):
    create = scripted_create(final(question=""))
    assert run(session, create)["question"] is None


def test_a_tool_that_fails_is_reported_to_the_model_not_raised(session):
    create = scripted_create(
        FakeResponse(calls=[("remove_exercise", {"date": "2026-09-25", "key": "09"})]),
        final(reply="There's no ninth exercise on that day."),
    )
    out = run(session, create)

    assert out["reply"] == "There's no ninth exercise on that day."
    output = json.loads([i for i in create.sent[1]["input"]
                         if i.get("type") == "function_call_output"][0]["output"])
    assert output["code"] == "not_found"


def test_an_unknown_tool_name_is_reported_rather_than_crashing(session):
    assert json.loads(session.run("drop_everything", "{}"))["error"].startswith("no tool called")


def test_arguments_that_are_not_json_are_reported(session):
    assert "valid JSON" in json.loads(session.run("add_exercise", "{oh dear"))["error"]


def test_the_day_context_describes_what_is_already_logged():
    day = {"date": "2026-09-25", "place": "home", "bodyweight": 180,
           "notes": "back fine", "summaryGeneratedAt": "2026-09-25T20:00:00Z",
           "exercises": {"01": {"order": 1, "exercise": "Plank", "group": "Core",
                                "muscles": ["Core"], "unit": "lb", "sets": [{"seconds": 45}] * 2}}}
    text = agent.day_context(day, "2026-09-25")

    assert "#1 key=01 Plank (Core): 2 × 45 s" in text
    assert "where: home" in text and "(guessed)" not in text
    assert "bodyweight: 180 lb" in text
    assert "summary: already written" in text


def test_today_means_today_even_with_another_day_open():
    # The first real use logged "glute bridge ... today" to the day the app
    # happened to have open, five days earlier.
    text = agent.build_context(today="2026-10-08", timezone="America/New_York", date="2026-10-03",
                               day=None, known_names=[], turns=[])
    assert "Log to 2026-10-03 unless the user names a day" in text
    assert '"Today" always means 2026-10-08.' in text


@pytest.mark.parametrize("word", ["Yash", " he ", " his ", " him ", "He "])
def test_the_prompt_is_about_any_user_not_one_person(word):
    # Every account gets the same prompt, and the context it reads with it.
    context = agent.build_context(today="2026-10-08", timezone="UTC", date="2026-10-08", day=None,
                                  known_names=["Plank"],
                                  turns=[{"transcript": "plank", "reply": "Logged.", "question": "How long?"}])
    for text in (prompts.ASSISTANT_SYSTEM, context, json.dumps(tools.SPECS)):
        assert word not in text


def test_an_empty_day_says_so():
    assert agent.day_context(None, "2026-09-25") == "2026-09-25: nothing logged yet."
