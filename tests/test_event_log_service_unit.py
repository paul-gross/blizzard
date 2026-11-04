""":class:`EventLogService`'s one authoring seam, ``record`` — severity is always derived
from the closed ``EventLogKind`` vocabulary, never paired independently, and a row written in a
caller's unit publishes nothing until the caller publishes the frame it was handed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

import pytest

from blizzard.hub.domain.chunk.event_log import EventLogService, LoggedEvent
from blizzard.hub.domain.chunk.ports.events import IEventLogPublisher, IWriteChunkEventsRepository
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.projects.model import ProjectId

pytestmark = pytest.mark.unit

_AT = datetime(2026, 8, 1, tzinfo=UTC)


@dataclass
class _RecordedRow:
    severity: str
    kind: str
    transaction: object = None


@dataclass
class _FakeEvents:
    rows: list[_RecordedRow] = field(default_factory=list[_RecordedRow])

    def record_event(
        self,
        *,
        severity: str,
        kind: str,
        runner_id: str | None,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict[str, object] | None,
        at: datetime,
        transaction: object = None,
    ) -> int:
        self.rows.append(_RecordedRow(severity=severity, kind=kind, transaction=transaction))
        return len(self.rows)


@dataclass
class _FakePublisher:
    published: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    projects: list[str | None] = field(default_factory=list[str | None])
    keys: list[str | None] = field(default_factory=list[str | None])

    def publish_event_logged(
        self,
        *,
        severity: str,
        kind: str,
        chunk_id: str | None,
        runner_id: str | None,
        runner_name: str | None = None,
        key: str | None = None,
        project_id: str | None = None,
    ) -> int:
        self.published.append((severity, kind))
        self.keys.append(key)
        self.projects.append(project_id)
        return len(self.published)


def _service(events: _FakeEvents, publisher: _FakePublisher) -> EventLogService:
    return EventLogService(
        events=cast(IWriteChunkEventsRepository, events),
        publisher=cast(IEventLogPublisher, publisher),
    )


def test_record_derives_severity_from_the_closed_vocabulary() -> None:
    events = _FakeEvents()
    service = _service(events, _FakePublisher())

    service.record(
        kind="worker-lost",
        runner_id="r1",
        chunk_id="ch_1",
        project_id=ProjectId("prj_1"),
        lease_id="lease_1",
        node_name="build",
        message="lost",
        detail=None,
        at=_AT,
    )

    assert events.rows == [_RecordedRow(severity="critical", kind="worker-lost")]


def test_the_frame_carries_the_project_the_caller_passed_and_a_row_naming_a_gone_chunk_still_records() -> None:
    events, publisher = _FakeEvents(), _FakePublisher()
    service = _service(events, publisher)

    for project_id in (ProjectId("prj_1"), None):
        service.record(
            kind="worker-lost",
            runner_id="r1",
            chunk_id="ch_gone",
            project_id=project_id,
            lease_id=None,
            node_name=None,
            message="lost",
            detail=None,
            at=_AT,
        )

    assert publisher.projects == [ProjectId("prj_1"), None]
    assert [row.kind for row in events.rows] == ["worker-lost", "worker-lost"]


def test_a_row_written_in_a_unit_publishes_only_when_its_frame_is_published() -> None:
    events, publisher = _FakeEvents(), _FakePublisher()
    service = _service(events, publisher)
    unit = cast(Transaction, object())

    logged = service.record(
        kind="repository-unresolved",
        runner_id=None,
        chunk_id="ch_1",
        project_id=ProjectId("prj_1"),
        lease_id=None,
        node_name="merge",
        message="unresolved",
        detail=None,
        at=_AT,
        transaction=unit,
    )

    assert events.rows == [_RecordedRow(severity="critical", kind="repository-unresolved", transaction=unit)]
    assert isinstance(logged, LoggedEvent) and logged.row_id == 1
    assert publisher.published == []

    service.publish(logged)

    assert publisher.published == [("critical", "repository-unresolved")]
    assert (publisher.projects, publisher.keys) == ([ProjectId("prj_1")], ["event_log:1"])
