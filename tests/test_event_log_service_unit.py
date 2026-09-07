""":class:`EventLogService`'s two authoring seams: the closed-vocabulary ``record`` every
in-process site now uses, and ``record_wire`` — the one escape hatch
``hub/domain/facts.py``'s ``EVENT_RECORDED`` branch calls, since a kind an older runner
minted may not be in this hub's vocabulary. That event must still land as written."""

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
        runner_id: str,
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
        self, *, severity: str, kind: str, chunk_id: str | None, runner_id: str, key: str | None = None
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


def test_record_wire_records_a_kind_unrecognized_by_this_hubs_vocabulary_as_written() -> None:
    """The version-skew guarantee: an older runner may mint a kind this hub's own
    vocabulary has never declared, and it must land exactly as sent, not be dropped or
    have its severity silently rewritten."""
    events = _FakeEvents()
    publisher = _FakePublisher()
    service = _service(events, publisher)

    row_id = service.record_wire(
        kind="a-kind-this-hub-has-never-heard-of",
        severity="warning",
        runner_id="r1",
        chunk_id="ch_1",
        lease_id=None,
        node_name=None,
        message="from an older runner",
        detail=None,
        at=_AT,
    )

    assert row_id == 1
    assert events.rows == [_RecordedRow(severity="warning", kind="a-kind-this-hub-has-never-heard-of")]
    assert publisher.published == [("warning", "a-kind-this-hub-has-never-heard-of")]
