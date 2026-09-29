"""shared/exercise_catalog.json is the one source of truth. It is copied into
the Lambda package and into web/ by `make sync-shared`; this test fails if a
copy drifts, which is the whole point of having the target."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from workoutlog import catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "shared" / "exercise_catalog.json"
COPIES = [
    REPO_ROOT / "backend" / "src" / "workoutlog" / "exercise_catalog.json",
    REPO_ROOT / "web" / "exercise_catalog.json",
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("copy", COPIES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_copies_match_the_source(copy):
    assert copy.exists(), f"{copy} is missing. Run: make sync-shared"
    assert _digest(copy) == _digest(SOURCE), f"{copy} has drifted. Run: make sync-shared"


def test_lookup_rules_are_well_formed():
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    groups, muscles = set(data["groups"]), set(data["muscles"])
    seen: set[str] = set()
    for rule in data["lookup"]:
        assert rule["group"] in groups, rule
        assert 1 <= len(rule["muscles"]) <= 4, rule
        for muscle in rule["muscles"]:
            assert muscle in muscles, rule
        for word in rule["words"]:
            assert word == word.lower().strip(), f"{word!r} should be lowercase"
            assert word not in seen, f"{word!r} appears in two rules"
            seen.add(word)


def test_every_common_name_resolves():
    """A suggestion the lookup cannot place would show up with no area."""
    from workoutlog import logic
    for name in catalog.common_names():
        assert logic.lookup(name) is not None, name


def test_main_areas_use_real_muscles():
    muscles = set(catalog.muscles())
    for area in catalog.main_areas():
        assert set(area["muscles"]) <= muscles, area
