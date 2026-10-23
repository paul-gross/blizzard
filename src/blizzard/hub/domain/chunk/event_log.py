"""Recording an operational event is what publishes it (``bzh:operational-event-log``):
one domain service holds both the write seam and the publisher seam, so every
event-authoring call site gets the live broadcast without knowing the broker exists."""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind, EventLogSeverity
from blizzard.hub.domain.chunk.ports.events import IEventLogPublisher, IWriteChunkEventsRepository
from blizzard.hub.domain.projects.model import ProjectId


class EventLogService:
    """Record one ``event_log`` row and publish it live, as one act. The caller hands in the project of the chunk the
    row names, which the frame publishes with it, so a project-filtered stream selects the frame without a read of its
    own; the row stores only the chunk id, so an event about a chunk that is gone still records."""

    def __init__(
        self,
        *,
        events: IWriteChunkEventsRepository,
        publisher: IEventLogPublisher,
    ) -> None:
        self._events = events
        self._publisher = publisher

    def record(
        self,
        *,
        kind: EventLogKind,
        runner_id: str | None,
        chunk_id: str | None,
        project_id: ProjectId | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict | None,
        at: datetime,
        runner_name: str | None = None,
    ) -> int:
        """Append the row, then publish an ``event-logged`` frame keyed off the row it
        just wrote. Returns the freshly-written ``event_log.id``. Severity is
        derived from ``kind`` (a function of it, never paired independently). The frame
        carries ``runner_name`` only when the caller hands it the runner's name; the row
        never stores it. ``project_id`` is the frame's, ``None`` for a row naming no chunk or one the caller
        could not resolve."""
        return self._record(
            severity=EVENT_LOG_SEVERITY[kind],
            kind=kind,
            runner_id=runner_id,
            runner_name=runner_name,
            chunk_id=chunk_id,
            project_id=project_id,
            lease_id=lease_id,
            node_name=node_name,
            message=message,
            detail=detail,
            at=at,
        )

    def _record(
        self,
        *,
        severity: EventLogSeverity,
        kind: str,
        runner_id: str | None,
        runner_name: str | None,
        chunk_id: str | None,
        project_id: ProjectId | None,
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
            severity=severity,
            kind=kind,
            chunk_id=chunk_id,
            runner_id=runner_id,
            runner_name=runner_name,
            key=f"event_log:{row_id}",
            project_id=project_id,
        )
        return row_id
