"""Loads shared/exercise_catalog.json.

The file ships inside the package (make sync-shared copies it here) so the
Lambda has no dependency on the repo layout at runtime.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_CATALOG_PATH = Path(__file__).with_name("exercise_catalog.json")


@lru_cache(maxsize=1)
def catalog() -> dict:
    with _CATALOG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def groups() -> list[str]:
    return catalog()["groups"]


def muscles() -> list[str]:
    return catalog()["muscles"]


def lookup_rules() -> list[dict]:
    return catalog()["lookup"]


def common_names() -> list[str]:
    return catalog()["commonNames"]


def main_areas() -> list[dict]:
    return catalog()["mainAreas"]


@lru_cache(maxsize=1)
def _muscle_index() -> dict[str, str]:
    """Lowercase muscle name -> canonical spelling, for case-insensitive input."""
    return {m.lower(): m for m in muscles()}


def canonical_muscle(name: str) -> str | None:
    return _muscle_index().get(name.strip().lower())
