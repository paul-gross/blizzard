"""Recording an operational event is what publishes it (``bzh:operational-event-log``):
one domain service holds both the write seam and the publisher seam, so every
event-authoring call site gets the live broadcast without knowing the broker exists."""

from __future__ import annotations

from datetime import datetime

from blizzard.hub.domain.chunks.events import IEventLogPublisher, IWriteChunkEventsRepository


class EventLogService:
    """Record one ``event_log`` row and publish it live, as one act."""

    def __init__(self, *, events: IWriteChunkEventsRepository, publisher: IEventLogPublisher) -> None:
        self._events = events
        self._publisher = publisher

    def record(
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
        """Append the row, then publish an ``event-logged`` frame keyed off the row it
        just wrote (issue #213). Returns the freshly-written ``event_log.id``."""
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
