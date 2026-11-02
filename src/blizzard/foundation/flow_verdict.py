"""A turn's verdict on its submission."""

from __future__ import annotations

from enum import StrEnum


class FlowVerdict(StrEnum):
    """A ``turn-ended`` entry's verdict on its submission."""

    LANDED = "landed"
    BOUNCED = "bounced"
