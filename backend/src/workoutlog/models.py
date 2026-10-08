"""Request and response models.

Every limit here exists so a bad or hostile request can't grow an item past
what DynamoDB and the AI prompt can carry. Exercise keys are 2 digits, which
is why a day holds at most 99 exercises.
"""

from __future__ import annotations

import re
from datetime import date as _date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import catalog, logic

MAX_EXERCISES_PER_DAY = 99
MAX_SETS_PER_EXERCISE = 50
MAX_SUMMARY = 600
MAX_DAY_NOTES = 1000
MAX_EXERCISE_NOTES = 300
MAX_SET_NOTE = 80
MAX_NAME = 80

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KEY_RE = re.compile(r"^\d{2}$")


def valid_date(value: str) -> str:
    """A calendar date in the user's local time, not a timestamp."""
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError("date should look like YYYY-MM-DD")
    year, month, day = (int(p) for p in value.split("-"))
    try:
        _date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"{value} is not a real date") from exc
    if not 2000 <= year <= 2100:
        raise ValueError("date should be between 2000 and 2100")
    return value


def valid_key(value: str) -> str:
    if not isinstance(value, str) or not _KEY_RE.match(value) or value == "00":
        raise ValueError("exercise key should be two digits, 01 to 99")
    return value


DateStr = Annotated[str, Field(min_length=10, max_length=10)]


class SetIn(BaseModel):
    """One set. Only the fields that apply are present, on the way in and out.

    A set with nothing measured is still a set: "did a set of glute bridges"
    gets logged as said, and the numbers can be filled in later."""

    model_config = ConfigDict(extra="forbid")

    reps: int | None = Field(default=None, ge=1, le=1000)
    repsMax: int | None = Field(default=None, ge=1, le=1000)
    weight: float | None = Field(default=None, ge=0, le=2000)
    seconds: float | None = Field(default=None, gt=0, le=7200)
    minutes: float | None = Field(default=None, gt=0, le=600)
    distance: float | None = Field(default=None, ge=0, le=1000)
    distanceUnit: Literal["mi", "km"] | None = None
    note: str | None = Field(default=None, max_length=MAX_SET_NOTE)

    @model_validator(mode="after")
    def _check(self) -> "SetIn":
        if self.repsMax is not None:
            if self.reps is None:
                raise ValueError("repsMax needs reps as well")
            if self.repsMax < self.reps:
                raise ValueError("repsMax should not be below reps")
        # A unit without a distance is noise; a distance without one defaults to miles.
        if self.distance is None:
            object.__setattr__(self, "distanceUnit", None)
        elif self.distanceUnit is None:
            object.__setattr__(self, "distanceUnit", "mi")
        if self.note is not None:
            object.__setattr__(self, "note", self.note.strip() or None)
        return self

    def stored(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class ExerciseIn(BaseModel):
    """What the client sends for POST and PUT. order and loggedAt are the
    server's to assign, so they are not accepted here."""

    model_config = ConfigDict(extra="forbid")

    exercise: str = Field(min_length=1, max_length=MAX_NAME)
    group: str | None = None
    muscles: list[str] | None = Field(default=None, max_length=4)
    unit: Literal["lb", "kg"] = "lb"
    perHand: bool | None = None
    sets: list[SetIn] = Field(min_length=1, max_length=MAX_SETS_PER_EXERCISE)
    notes: str | None = Field(default=None, max_length=MAX_EXERCISE_NOTES)

    @field_validator("exercise")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("enter the exercise name")
        return cleaned

    @field_validator("group")
    @classmethod
    def _group(cls, value: str | None) -> str | None:
        if value is None:
            return None
        for known in catalog.groups():
            if known.lower() == value.strip().lower():
                return known
        raise ValueError("group should be one of: " + ", ".join(catalog.groups()))

    @field_validator("muscles")
    @classmethod
    def _muscles(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        out: list[str] = []
        for raw in value:
            name = catalog.canonical_muscle(raw or "")
            if name is None:
                raise ValueError(
                    f"unknown muscle {raw!r}. Valid muscles: " + ", ".join(catalog.muscles())
                )
            if name not in out:
                out.append(name)
        return out or None

    @model_validator(mode="after")
    def _notes(self) -> "ExerciseIn":
        if self.notes is not None:
            object.__setattr__(self, "notes", self.notes.strip() or None)
        return self

    def stored(self, order: int, logged_at: str) -> dict[str, Any]:
        """Fill a missing group or muscles from the catalog, so stored items are
        always complete and the frontend never has to guess."""
        found = logic.lookup(self.exercise)
        group = self.group or (found["group"] if found else "Mobility")
        muscles = self.muscles or (list(found["muscles"]) if found else [])
        item: dict[str, Any] = {
            "order": order,
            "exercise": self.exercise,
            "group": group,
            "muscles": muscles,
            "unit": self.unit,
            "sets": [s.stored() for s in self.sets],
            "loggedAt": logged_at,
        }
        if self.perHand:
            item["perHand"] = True
        if self.notes:
            item["notes"] = self.notes
        return item


class DayPatch(BaseModel):
    """PATCH /v1/days/{date}. A field that is absent is left alone; a field
    sent as null is removed. Pydantic's fields_set tells the two apart."""

    model_config = ConfigDict(extra="forbid")

    place: Literal["gym", "home"] | None = None
    notes: str | None = Field(default=None, max_length=MAX_DAY_NOTES)
    bodyweight: float | None = Field(default=None, ge=30, le=1000)
    summary: str | None = Field(default=None, max_length=MAX_SUMMARY)

    @model_validator(mode="after")
    def _trim(self) -> "DayPatch":
        for field in ("notes", "summary"):
            value = getattr(self, field)
            if isinstance(value, str):
                object.__setattr__(self, field, value.strip() or None)
        if not self.model_fields_set:
            raise ValueError("send at least one of place, notes, bodyweight or summary")
        return self

    def changes(self) -> dict[str, Any]:
        """Only the fields the caller actually mentioned, None meaning remove."""
        return {name: getattr(self, name) for name in self.model_fields_set}


class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    toDate: str

    @field_validator("toDate")
    @classmethod
    def _date(cls, value: str) -> str:
        return valid_date(value)


MAX_TEXT = 500
MAX_AUDIO_BYTES = 2 * 1024 * 1024
AUDIO_TYPES = ("audio/mp4", "audio/webm")


class AssistantIn(BaseModel):
    """One voice or typed turn. Either audio or text, never both and never
    neither: the route has nothing to send to the model otherwise."""

    model_config = ConfigDict(extra="forbid")

    audioBase64: str | None = None
    audioMimeType: str | None = None
    text: str | None = Field(default=None, max_length=MAX_TEXT)
    date: DateStr
    today: DateStr
    timezone: str = Field(default="UTC", max_length=64)

    @field_validator("date", "today")
    @classmethod
    def _dates(cls, value: str) -> str:
        return valid_date(value)

    @model_validator(mode="after")
    def _one_input(self) -> "AssistantIn":
        said = (self.text or "").strip()
        if said:
            object.__setattr__(self, "text", said)
        if bool(self.audioBase64) == bool(said):
            raise ValueError("send either audio or text")
        if self.audioBase64:
            mime = (self.audioMimeType or "").split(";")[0].strip().lower()
            if mime not in AUDIO_TYPES:
                raise ValueError("audioMimeType should be audio/mp4 or audio/webm")
            object.__setattr__(self, "audioMimeType", mime)
        return self

    def audio(self) -> bytes:
        """Decoded once, here, so the size limit is enforced on real bytes
        rather than on the base64 text that carries them."""
        import base64
        import binascii
        try:
            raw = base64.b64decode(self.audioBase64 or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("audioBase64 is not valid base64") from exc
        if not raw:
            raise ValueError("the recording was empty")
        if len(raw) > MAX_AUDIO_BYTES:
            raise ValueError("that recording is too long. Keep it under 60 seconds")
        return raw


class UndoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    undoToken: str = Field(min_length=1, max_length=120)


class FinishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=MAX_DAY_NOTES)
    timezone: str | None = Field(default=None, max_length=64)
