"""The chunk-events repository seam — the operational event log and the
cross-table activity feed derived from it and the other concepts' own fact tables."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.work import DEFAULT_EVENT_LIST_LIMIT, ActivityRow, EventRow


class IReadChunkEventsRepository(Protocol):
    """Read-only chunk-events access."""

    def list_events(
        self,
        *,
        severity: str | None = None,
        runner_id: str | None = None,
        chunk_id: str | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_EVENT_LIST_LIMIT,
    ) -> list[EventRow]:
        """The operational event log, newest-first (``recorded_at`` desc, ``id`` desc
        tiebreak), filtered by whichever of ``severity``/``runner_id``/``chunk_id``/
        ``since`` is given and bounded by ``limit`` — ``GET /api/events``'s own-table
        half (issue #125); the caller unifies it with ``list_open_escalations`` via
        :class:`~blizzard.hub.domain.work.EventFeed`."""
        ...

    def activity_facts_since(self, since: datetime, *, limit: int) -> list[ActivityRow]:
        """Every ``chunk-changed``-shaped activity row across every mapped cause's fact
        table, at or after ``since`` (issue #213, AC4). ``edited`` is deliberately
        unrepresented: a chunk edit writes no fact row — a documented exclusion, not a
        gap. Each source table is read with its own bounded ``ORDER BY … LIMIT``, so this
        returns rows unsorted across sources."""
        ...


class IWriteChunkEventsRepository(IReadChunkEventsRepository, Protocol):
    """Read-write chunk-events access."""

    def record_event(
        self,
        *,
        severity: str,
        kind: str,
        runner_id: str,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict | None,
        at: datetime,
    ) -> int:
        """Append one ``event_log`` row (issue #125) — never mutated once written.

        ``chunk_id`` is ``None`` for a runner-scoped event; ``detail`` is an opaque
        event-specific payload, serialized to JSON text by the store. Returns the
        freshly-written ``event_log.id``."""
        ...
