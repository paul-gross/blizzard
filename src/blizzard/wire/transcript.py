"""Transcript wire bodies — ``GET /api/leases/{lease_id}/transcript`` (issue #29, blizzard#249).

A turn's ``timestamp`` is an ISO-8601 string with an explicit UTC offset, never naive
(``bzh:utc-instants``). ``available=False`` carries ``reason`` and an empty ``turns``.
``provenance``/``hub_unreachable``/``dropped_turns`` are blizzard#249's home-selection growth
(D1, D5), on every response; a local read's is always ``"local"``/``False``/``0``."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.runner.transcripts.repository import TranscriptUnavailable, TurnKind
from blizzard.runner.transcripts.service import TranscriptProvenance


class TurnView(BaseModel):
    """One collapsed conversation turn on the wire."""

    index: int
    kind: TurnKind
    timestamp: str | None
    text: str
    tool_name: str | None
    tool_input: str | None
    tool_output: str | None
    truncated: bool


class TranscriptResponse(BaseModel):
    """A lease's parsed transcript — always 200 when the lease exists."""

    lease_id: str
    session_id: str | None
    available: bool
    reason: TranscriptUnavailable | None
    turns: list[TurnView] = []
    truncated: bool = False
    #: Which side answered (D1) — always ``"local"`` for an open lease's read.
    provenance: TranscriptProvenance = "local"
    #: Set only when a closed lease's hub could not be asked *and* local cannot answer
    #: either (D1) — the panel's distinct hub-unreachable state.
    hub_unreachable: bool = False
    #: How many turns the hub→panel projection dropped (D5) — zero on every local read.
    dropped_turns: int = 0
