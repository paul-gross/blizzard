"""Transcript wire bodies — ``GET /api/leases/{lease_id}/transcript`` (issue #29).

Mirrors ``wire/lease.py``'s shape and its ``iso_utc`` discipline: a turn's
``timestamp`` is an ISO-8601 string with an explicit UTC offset, never naive
(``bzh:utc-instants``) — serialized by ``api/transcripts.py``'s ``_view``.

``available=False`` carries ``reason`` and an empty ``turns`` —
this is a normal 200, not an error shape; the route never returns a 5xx for a
missing or unreadable transcript. Modeled on
:class:`~blizzard.runner.transcripts.repository.Transcript`/``Turn``.
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
