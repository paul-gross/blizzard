"""Recording an operational event is what publishes it (``bzh:operational-event-log``):
one domain service holds both the write seam and the publisher seam, so every
event-authoring call site gets the live broadcast without knowing the broker exists."""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.hub.domain.chunks.events import IEventLogPublisher, IWriteChunkEventsRepository


class EventLogService:
    """Record one ``event_log`` row and publish it live, as one act."""

    def __init__(self, *, events: IWriteChunkEventsRepository, publisher: IEventLogPublisher) -> None:
        self._events = events
        self._publisher = publisher

    def record(
        self,
        *,
        kind: EventLogKind,
        runner_id: str,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict | None,
        at: datetime,
    ) -> int:
        """Append the row, then publish an ``event-logged`` frame keyed off the row it
        just wrote (issue #213). Returns the freshly-written ``event_log.id``. Severity is
        derived from ``kind`` (a function of it, never paired independently)."""
        return self._record(
            severity=EVENT_LOG_SEVERITY[kind],
            kind=kind,
            runner_id=runner_id,
            chunk_id=chunk_id,
            lease_id=lease_id,
            node_name=node_name,
            message=message,
            detail=detail,
            at=at,
        )

    def record_wire(
        self,
        *,
        kind: str,
        severity: str,
        runner_id: str,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict | None,
        at: datetime,
    ) -> int:
        """The one escape hatch for a ``kind`` this hub's vocabulary may not recognize —
        minted by an older runner (``hub/domain/facts.py``'s ``EVENT_RECORDED`` branch). That
        event must still land, so it is recorded as written, with the severity read off the
        wire alongside it, never rejected or derived from a table that may not have an entry
        for it."""
        return self._record(
            severity=severity,
            kind=kind,
            runner_id=runner_id,
            chunk_id=chunk_id,
            lease_id=lease_id,
            node_name=node_name,
            message=message,
            detail=detail,
            at=at,
        )

    def _record(
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
        row_id = self._events.record_event(
            severity=severity,
            kind=kind,
            runner_id=runner_id,
            chunk_id=chunk_id,
            lease_id=lease_id,
            node_name=node_name,
            message=message,
            detail=detail,
            at=at,
        )
        self._publisher.publish_event_logged(
            severity=severity, kind=kind, chunk_id=chunk_id, runner_id=runner_id, key=f"event_log:{row_id}"
        )
        return row_id
