"""SQLAlchemy adapter for the egress export seam (package-private).

Reads the cursor and its latch and appends the cursor's fact rows (``bzh:facts-not-status``). Usage facts past a cursor
position span every tenant, so they come from the sweep corpus (:class:`IReadUsageCorpus`); closed steps are read
through :class:`~blizzard.hub.store.internal.trace_store.TraceStore`, the trace sweep's own reader."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, insert, select

from blizzard.hub.domain.observability.egress.config import EGRESS_DATASETS
from blizzard.hub.domain.observability.egress.repository import (
    EgressCheckpoint,
    EgressWriteFailure,
    EventsPosition,
    IReadUsageCorpus,
    IWriteEgressCursor,
    UsagePosition,
)
from blizzard.hub.domain.observability.egress.rows import AttributedUsage
from blizzard.hub.domain.observability.hub_events import HubEventKind, IReadHubEventLog
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.tenancy import TenantId

_FAILED: HubEventKind = "egress-write-failed"
_LATCH_KINDS: tuple[HubEventKind, ...] = (_FAILED, "egress-write-recovered")
_NO_FILES = json.dumps([])


class EgressStore:
    """The egress export's usage reads and its cursor's appends. The cursor is hub-level, and so are the export's
    failure and recovery events, read from ``hub_events``, the hub's own event log its sweep records them in."""

    def __init__(self, store: HubStoreConnections, *, hub_events: IReadHubEventLog, usage: IReadUsageCorpus) -> None:
        self._store = store
        self._hub_events = hub_events
        self._usage = usage

    def newest_cursor(self, dataset: str) -> EgressCheckpoint | None:
        c = s.egress_cursor.c
        stmt = select(s.egress_cursor).where(c.dataset == dataset).order_by(c.recorded_at.desc(), c.id.desc()).limit(1)
        with self._store.read("egress_newest_cursor") as conn:
            row = conn.execute(stmt).first()
        return _cursor(row) if row is not None else None

    def newest_cursor_with_files(self) -> EgressCheckpoint | None:
        """The newest row that placed files across the datasets — one indexed read per dataset."""
        c = s.egress_cursor.c
        newest: list[EgressCheckpoint] = []
        for dataset in EGRESS_DATASETS:
            stmt = (
                select(s.egress_cursor)
                .where(c.dataset == dataset, c.files != _NO_FILES)
                .order_by(c.recorded_at.desc(), c.id.desc())
                .limit(1)
            )
            with self._store.read("egress_newest_cursor_with_files") as conn:
                row = conn.execute(stmt).first()
            if row is not None:
                newest.append(_cursor(row))
        return max(newest, key=lambda record: record.recorded_at, default=None)

    def newest_egress_failure(self) -> EgressWriteFailure | None:
        """The newest failure event — filtered in the query, so a later recovery never hides it."""
        event = self._hub_events.newest((_FAILED,))
        return EgressWriteFailure(event.at, event.message) if event is not None else None

    def usage_after(self, position: UsagePosition, until: datetime, limit: int) -> Sequence[AttributedUsage]:
        return self._usage.usage_after(position, until, limit)

    def newest_egress_latch(self) -> HubEventKind | None:
        """The newer of the two latch kinds' newest events."""
        event = self._hub_events.newest(_LATCH_KINDS)
        return next((k for k in _LATCH_KINDS if event is not None and k == event.kind), None)

    def append_cursor(self, record: EgressCheckpoint) -> None:
        step = record.step
        events = record.events
        with self._store.write("egress_append_cursor") as conn:
            conn.execute(
                insert(s.egress_cursor).values(
                    dataset=record.dataset,
                    position_at=step.at if step is not None else events.at if events is not None else None,
                    chunk_id=step.chunk_id if step is not None else None,
                    epoch=step.epoch if step is not None else None,
                    decision_id=step.decision_id if step is not None else None,
                    segment_id=events.segment_id if events is not None else None,
                    extractor_version=events.extractor_version if events is not None else None,
                    events_tenant_id=events.tenant if events is not None else None,
                    usage_recorded_at=record.usage.recorded_at,
                    usage_id=record.usage.usage_id,
                    row_count=record.row_count,
                    files=json.dumps(list(record.files)),
                    recorded_at=record.recorded_at,
                )
            )


def _cursor(row: Row[Any]) -> EgressCheckpoint:
    events = (
        EventsPosition(row.position_at, row.segment_id, row.extractor_version or "", _tenant(row.events_tenant_id))
        if row.segment_id is not None
        else None
    )
    step = (
        CursorKey(row.position_at, row.chunk_id or "", row.epoch or 0, row.decision_id or "")
        if row.position_at is not None and events is None
        else None
    )
    return EgressCheckpoint(
        dataset=row.dataset,
        step=step,
        usage=UsagePosition(row.usage_recorded_at, row.usage_id),
        row_count=row.row_count,
        files=tuple(json.loads(row.files)),
        recorded_at=row.recorded_at,
        events=events,
    )


def _conforms_egress_store(x: EgressStore) -> IWriteEgressCursor:
    return x


def _tenant(value: str | None) -> TenantId | None:
    """The events position's tenant; a position recorded without one stands past every tenant at its time,
    segment and version."""
    return TenantId(value) if value is not None else None
