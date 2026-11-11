"""Runner selftest responses."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel

from blizzard.foundation.selftest import SelfTestStatus
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


class SelfTestCheckView(BaseModel):
    """One check's pass/fail result."""

    name: str
    passed: bool
    detail: str


class SelfTestView(WireModel):
    """A selftest run's current state — ``running`` until every check has resolved, then ``settled``."""

    id: str
    harness: str
    status: Annotated[SelfTestStatus | Unrecognized, OPEN]
    # The run has concluded — no further status move is possible.
    settled: bool
    checks: list[SelfTestCheckView]
    error: str | None = None
