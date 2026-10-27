"""How the host starts its passes: on timers, or only on request."""

from __future__ import annotations

from enum import StrEnum


class LoopMode(StrEnum):
    """``timed`` runs every pass on its timer; ``stepped`` starts none, and a pass runs only when requested."""

    TIMED = "timed"
    STEPPED = "stepped"
