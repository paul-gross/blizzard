"""Recording an operational event is what publishes it (``bzh:operational-event-log``):
one domain service holds both the write seam and the publisher seam, so every
event-authoring call site gets the live broadcast without knowing the broker exists. A row
written inside a caller's unit hands its frame back instead, for the caller to publish once
the unit commits (``bzh:publish-after-commit``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import overload

from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind, EventLogSeverity
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.ports.events import IEventLogPublisher, IWriteChunkEventsRepository
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.projects.model import ProjectId


@domain_model
@dataclass(frozen=True)
class LoggedEvent:
    """An ``event_log`` row a unit wrote, and the ``event-logged`` frame :meth:`EventLogService.publish` announces
    for it once that unit has committed."""

    row_id: int
    severity: EventLogSeverity
    kind: str
    chunk_id: str | None
    runner_id: str | None
    runner_name: str | None
    project_id: ProjectId | None


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

    @overload
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
        detail: Mapping[str, object] | None,
        at: datetime,
        runner_name: str | None = None,
        transaction: None = None,
    ) -> int: ...

    @overload
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
        detail: Mapping[str, object] | None,
        at: datetime,
        runner_name: str | None = None,
        transaction: Transaction,
    ) -> LoggedEvent: ...

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
        detail: Mapping[str, object] | None,
        at: datetime,
        runner_name: str | None = None,
        transaction: Transaction | None = None,
    ) -> int | LoggedEvent:
        """Append the row, then publish an ``event-logged`` frame keyed off the row it
        just wrote. Returns the freshly-written ``event_log.id``. Severity is
        derived from ``kind`` (a function of it, never paired independently). The frame
        carries ``runner_name`` only when the caller hands it the runner's name; the row
        never stores it. ``project_id`` is the frame's, ``None`` for a row naming no chunk or one the caller
        could not resolve.

        With ``transaction``, the row is written in that unit and nothing is published: the
        :class:`LoggedEvent` returned is the frame, for the caller to :meth:`publish` once the unit commits."""
        severity = EVENT_LOG_SEVERITY[kind]
        row_id = self._events.record_event(
            severity=severity,
            kind=kind,
            runner_id=runner_id,
            chunk_id=chunk_id,
            lease_id=lease_id,
            node_name=node_name,
            message=message,
            detail=dict(detail) if detail is not None else None,
            at=at,
            transaction=transaction,
        )
        logged = LoggedEvent(
            row_id=row_id,
            severity=severity,
            kind=kind,
            chunk_id=chunk_id,
            runner_id=runner_id,
            runner_name=runner_name,
            project_id=project_id,
        )
        if transaction is not None:
            return logged
        self.publish(logged)
        return row_id

    def publish(self, *logged: LoggedEvent) -> None:
        """Publish each row's ``event-logged`` frame, in order — called once the unit that wrote them committed."""
        for event in logged:
            self._publisher.publish_event_logged(
                severity=event.severity,
                kind=event.kind,
                chunk_id=event.chunk_id,
                runner_id=event.runner_id,
                runner_name=event.runner_name,
                key=f"event_log:{event.row_id}",
                project_id=event.project_id,
            )
