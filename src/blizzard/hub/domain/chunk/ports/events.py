"""The chunk-events repository seam — the operational event log and the
cross-table activity feed derived from it and the other concepts' own fact tables."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.foundation.event_log import EventLogSeverity
from blizzard.hub.domain.chunk.model import DEFAULT_EVENT_LIST_LIMIT, ActivityEntry, OperationalEvent


class IReadChunkEventsRepository(Protocol):
    """Read-only chunk-events access."""

    def list_events(
        self,
        *,
        severity: EventLogSeverity | None = None,
        runner_id: str | None = None,
        chunk_id: str | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_EVENT_LIST_LIMIT,
    ) -> list[OperationalEvent]:
        """The operational event log, newest first (``recorded_at`` desc, ``id`` desc
        tiebreak), filtered by whichever of ``severity``/``runner_id``/``chunk_id``/``since``
        is given and bounded by ``limit`` — the cap keeps the newest rows, whatever their
        severity."""
        ...

    def activity_facts_since(self, since: datetime, *, limit: int) -> list[ActivityEntry]:
        """Every ``chunk-changed``-shaped activity row across every mapped cause's fact
        table, at or after ``since`` (AC4). ``edited`` is deliberately
        unrepresented: a chunk edit writes no fact row — a documented exclusion, not a
        gap. Each source table is read with its own bounded ``ORDER BY … LIMIT``, so this
        returns rows unsorted across sources."""
        ...

    def activity_events_since(self, since: datetime, *, limit: int) -> list[OperationalEvent]:
        """``event_log`` rows for the activity feed's ``event-logged`` half — newest
        first (``recorded_at`` desc, ``id`` desc tiebreak), a deleted chunk's rows
        excluded, bounded by ``limit`` after that ordering. A runner-scoped row
        (``chunk_id IS NULL``) is never excluded by the deleted-chunk filter.

        Distinct from :meth:`list_events`: this read also excludes a deleted chunk's rows."""
        ...


class IWriteChunkEventsRepository(IReadChunkEventsRepository, Protocol):
    """Read-write chunk-events access."""

    def record_event(
        self,
        *,
        severity: EventLogSeverity,
        kind: str,
        runner_id: str | None,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict | None,
        at: datetime,
    ) -> int:
        """Append one ``event_log`` row — never mutated once written.

        ``chunk_id``/``runner_id`` are ``None`` for a runner-scoped/hub-authored event,
        respectively; ``detail`` is opaque, serialized to JSON text by the store. Returns
        the freshly-written ``event_log.id``."""
        ...


class IEventLogPublisher(Protocol):
    """The live-broadcast half of recording an event — one operation: broadcasting that
    an event was logged, live, to whoever is subscribed."""

    def publish_event_logged(
        self,
        *,
        severity: EventLogSeverity,
        kind: str,
        chunk_id: str | None,
        runner_id: str | None,
        runner_name: str | None = None,
        key: str | None = None,
    ) -> int:
        """Fan out one ``event-logged`` SSE frame; ``runner_name`` is omitted from it when ``None``."""
        ...
