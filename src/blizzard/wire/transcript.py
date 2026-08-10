"""Transcript wire bodies — ``GET /api/leases/{lease_id}/transcript`` (issue #29, blizzard#249).

A turn's ``timestamp`` is an ISO-8601 string with an explicit UTC offset, never naive
(``bzh:utc-instants``). ``available=False`` carries ``reason`` and an empty ``turns``.
``TurnView`` is retired (blizzard#248 D1) for ``transcript_segment.py``'s ``TurnSegmentView``,
the same shape the hub's segment-content route serves — one viewer renders both."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.runner.transcripts.repository import TranscriptProvenance, TranscriptUnavailable
from blizzard.wire.transcript_segment import TurnSegmentView


class TranscriptResponse(BaseModel):
    """A lease's parsed transcript — always 200 when the lease exists."""

    lease_id: str
    session_id: str | None
    available: bool
    reason: TranscriptUnavailable | None
    turns: list[TurnSegmentView] = []
    truncated: bool = False
    #: Which side answered (D1) — always ``"local"`` for an open lease's read.
    provenance: TranscriptProvenance = "local"
    #: Set only when a closed lease's hub could not be asked *and* local cannot answer
    #: either (D1) — the panel's distinct hub-unreachable state.
    hub_unreachable: bool = False
