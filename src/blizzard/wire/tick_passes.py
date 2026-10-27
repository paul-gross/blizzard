"""One requested runner pass's answer — how it ran, once it has finished."""

from __future__ import annotations

from typing import Annotated

from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


class TickPassResponse(WireModel):
    """A finished runner or host pass: when it started and finished on the runner's clock, its outcome, and the
    fault's message when it failed — ``null`` when it completed."""

    started_at: str
    finished_at: str
    outcome: Annotated[SweepPassOutcome | Unrecognized, OPEN]
    error: str | None
