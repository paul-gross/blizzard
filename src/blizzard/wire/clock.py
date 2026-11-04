"""The controlled clock's wire shapes, shared by every daemon that serves a clock route behind the test fence."""

from __future__ import annotations

import math
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from blizzard.wire.base import WireModel


class ClockReading(WireModel):
    now: str


class ClockMoveResponse(WireModel):
    previous: str
    now: str


class ClockMoveRequest(BaseModel):
    """Exactly one of an absolute instant or a non-negative advance in seconds."""

    model_config = ConfigDict(extra="forbid")

    to: AwareDatetime | None = None
    advance_seconds: float | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if len(self.model_fields_set) != 1 or (self.to is None) == (self.advance_seconds is None):
            raise ValueError("give exactly one of `to` and `advance_seconds`")
        if self.advance_seconds is not None and not (math.isfinite(self.advance_seconds) and self.advance_seconds >= 0):
            raise ValueError("`advance_seconds` must be finite and not negative")
        return self
