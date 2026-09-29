"""Optimistic locking, the ETag counter, and Decimal round-tripping."""

from __future__ import annotations

from decimal import Decimal

import pytest

from workoutlog.errors import ApiError
from workoutlog.repo import from_dynamo, to_dynamo

SUB = "user-1"
DATE = "2026-09-25"


def _day(**extra):
    return {"date": DATE, "exercises": {
        "01": {"order": 1, "exercise": "Plank", "group": "Core", "muscles": ["Core"],
               "unit": "lb", "sets": [{"seconds": 30}], "loggedAt": "2026-09-25T00:00:00Z"}},
        **extra}


def test_version_and_data_version_rise_together(repo):
    assert repo.data_version(SUB) == 0
    day = repo.mutate_day(SUB, DATE, lambda _d: _day())
    assert day["version"] == 1 and repo.data_version(SUB) == 1

    day = repo.mutate_day(SUB, DATE, lambda d: {**d, "notes": "sore"})
    assert day["version"] == 2 and repo.data_version(SUB) == 2


def test_a_competing_write_forces_a_retry_and_nothing_is_lost(repo):
    repo.mutate_day(SUB, DATE, lambda _d: _day())
    attempts = []

    def mine(day):
        attempts.append(dict(day))
        if len(attempts) == 1:
            # Another request lands between our read and our conditional write.
            repo.mutate_day(SUB, DATE, lambda d: {**d, "bodyweight": 180})
        day["notes"] = "mine"
        return day

    result = repo.mutate_day(SUB, DATE, mine)

    assert len(attempts) == 2, "the first write should have been rejected"
    assert result["notes"] == "mine"
    assert result["bodyweight"] == 180, "the competing write must survive"
    assert result["version"] == 3


def test_it_gives_up_after_three_attempts(repo):
    repo.mutate_day(SUB, DATE, lambda _d: _day())
    attempts = []

    def always_loses(day):
        attempts.append(1)
        repo.mutate_day(SUB, DATE, lambda d: {**d, "notes": f"other {len(attempts)}"})
        return {**day, "notes": "mine"}

    with pytest.raises(ApiError) as caught:
        repo.mutate_day(SUB, DATE, always_loses)

    assert len(attempts) == 3
    assert caught.value.status == 409 and caught.value.code == "version_conflict"
    assert repo.get_day(SUB, DATE)["notes"] == "other 3"


def test_deleting_a_day_also_bumps_the_etag(repo):
    repo.mutate_day(SUB, DATE, lambda _d: _day())
    before = repo.data_version(SUB)
    assert repo.mutate_day(SUB, DATE, lambda _d: None) is None
    assert repo.get_day(SUB, DATE) is None
    assert repo.data_version(SUB) == before + 1


def test_move_is_atomic_across_two_days(repo):
    repo.mutate_day(SUB, DATE, lambda _d: _day())
    target = repo.move_exercise(SUB, DATE, "2026-09-26", "01")
    assert target["exercises"]["01"]["exercise"] == "Plank"
    assert repo.get_day(SUB, DATE) is None
    assert repo.data_version(SUB) == 2


def test_numbers_survive_the_round_trip(repo):
    day = _day()
    day["exercises"]["01"]["sets"] = [{"reps": 10, "weight": 42.5, "distance": 1.07}]
    day["bodyweight"] = 180.4
    stored = repo.mutate_day(SUB, DATE, lambda _d: day)
    got = stored["exercises"]["01"]["sets"][0]
    assert got == {"reps": 10, "weight": 42.5, "distance": 1.07}
    assert isinstance(got["reps"], int) and isinstance(got["weight"], float)
    assert stored["bodyweight"] == 180.4


def test_decimal_helpers():
    assert from_dynamo({"a": Decimal("40"), "b": Decimal("1.07")}) == {"a": 40, "b": 1.07}
    assert to_dynamo(1.3) == Decimal("1.3")            # not the binary float tail
    assert to_dynamo({"keep": True, "drop": None}) == {"keep": True}


def test_usage_counter_is_atomic(repo):
    assert [repo.bump_usage(SUB, DATE) for _ in range(3)] == [1, 2, 3]
    repo.release_usage(SUB, DATE)
    assert repo.bump_usage(SUB, DATE) == 3
