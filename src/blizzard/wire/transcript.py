"""Transcript wire bodies — ``GET /api/leases/{lease_id}/transcript`` (issue #29).

A turn's ``timestamp`` is an ISO-8601 string with an explicit UTC offset, never naive
(``bzh:utc-instants``).

``available=False`` carries ``reason`` and an empty ``turns`` — a normal 200, not an
error shape.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.runner.transcripts.repository import TranscriptUnavailable, TurnKind


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
