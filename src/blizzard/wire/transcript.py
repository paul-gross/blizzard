"""The transcript wire body: ``TranscriptResponse``, a lease's parsed transcript."""

from __future__ import annotations

from typing import Annotated

from blizzard.foundation.transcripts import TranscriptProvenance, TranscriptUnavailable
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.transcript_segment import TurnSegmentView
from blizzard.wire.vocabulary import OPEN


class TranscriptResponse(WireModel):
    """A lease's parsed transcript — 200 when the lease exists and its recorded harness
    owner is known and available; 503 when that owner is unknown or unavailable.
    ``available=False`` carries ``reason`` and an empty ``turns``."""

    lease_id: str
    session_id: str | None
    available: bool
    reason: Annotated[TranscriptUnavailable | Unrecognized, OPEN] | None
    turns: list[TurnSegmentView] = []
    truncated: bool = False
    #: Which side answered — always ``"local"`` for an open lease's read.
    provenance: Annotated[TranscriptProvenance | Unrecognized, OPEN] = "local"
    #: Set only when a closed lease's hub could not be asked *and* local cannot answer
    #: either.
    hub_unreachable: bool = False
