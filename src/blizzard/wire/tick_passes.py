from __future__ import annotations

from typing import Annotated

from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


class TickPassResponse(WireModel):
    started_at: str
    finished_at: str
    outcome: Annotated[SweepPassOutcome | Unrecognized, OPEN]
    error: str | None
