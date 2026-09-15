""":class:`EventLogService`'s one authoring seam, ``record`` — severity is always derived
from the closed ``EventLogKind`` vocabulary, never paired independently."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

import pytest

from blizzard.hub.domain.chunks.events import IEventLogPublisher, IWriteChunkEventsRepository
from blizzard.hub.domain.event_log import EventLogService

pytestmark = pytest.mark.unit

_AT = datetime(2026, 8, 1, tzinfo=UTC)


@dataclass
class _RecordedRow:
    severity: str
    kind: str


@dataclass
class _FakeEvents:
    rows: list[_RecordedRow] = field(default_factory=list)

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
        detail: dict | None,
        at: datetime,
    ) -> int:
        self.rows.append(_RecordedRow(severity=severity, kind=kind))
        return len(self.rows)


@dataclass
class _FakePublisher:
    published: list[tuple[str, str]] = field(default_factory=list)

    def publish_event_logged(
        self, *, severity: str, kind: str, chunk_id: str | None, runner_id: str | None, key: str | None = None
    ) -> int:
        self.published.append((severity, kind))
        return len(self.published)


def _service(events: _FakeEvents, publisher: _FakePublisher) -> EventLogService:
    return EventLogService(
        events=cast(IWriteChunkEventsRepository, events), publisher=cast(IEventLogPublisher, publisher)
    )


def test_record_derives_severity_from_the_closed_vocabulary() -> None:
    events = _FakeEvents()
    service = _service(events, _FakePublisher())

    service.record(
        kind="worker-lost",
        runner_id="r1",
        chunk_id="ch_1",
        lease_id="lease_1",
        node_name="build",
        message="lost",
        detail=None,
        at=_AT,
    )

    assert events.rows == [_RecordedRow(severity="critical", kind="worker-lost")]
