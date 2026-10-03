"""SQLAlchemy adapter for the egress export seam (package-private).

Reads usage facts past a cursor position and appends the cursor's fact rows (``bzh:facts-not-status``). Closed
steps are read through :class:`~blizzard.hub.store.internal.trace_store.TraceStore`, the trace sweep's own reader."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, insert, or_, select

from blizzard.foundation.event_log import EventLogKind
from blizzard.hub.config import EGRESS_DATASETS
from blizzard.hub.domain.egress.repository import (
    EgressCursorRecord,
    EgressFailureRecord,
    IWriteEgressCursor,
    UsagePosition,
)
from blizzard.hub.domain.egress.rows import UsageRow
from blizzard.hub.domain.tracing.cursor import CursorKey
from blizzard.hub.domain.work import UsageFact
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections

_FAILED: EventLogKind = "egress-write-failed"
_LATCH_KINDS: tuple[EventLogKind, ...] = (_FAILED, "egress-write-recovered")
_NO_FILES = json.dumps([])


class EgressStore:
    """The egress export's usage reads and its cursor's appends."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def newest_cursor(self, dataset: str) -> EgressCursorRecord | None:
        c = s.egress_cursor.c
        stmt = select(s.egress_cursor).where(c.dataset == dataset).order_by(c.recorded_at.desc(), c.id.desc()).limit(1)
        with self._store.read("egress_newest_cursor") as conn:
            row = conn.execute(stmt).first()
        return _cursor(row) if row is not None else None

    def newest_cursor_with_files(self) -> EgressCursorRecord | None:
        """The newest row that placed files across the datasets — one indexed read per dataset."""
        c = s.egress_cursor.c
        newest: list[EgressCursorRecord] = []
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

    def newest_egress_failure(self) -> EgressFailureRecord | None:
        """Walks the latch events newest-first — one per outage edge, so a few — to the first failure."""
        c = s.event_log.c
        stmt = (
            select(c.kind, c.recorded_at, c.message)
            .where(c.kind.in_(_LATCH_KINDS))
            .order_by(c.recorded_at.desc(), c.id.desc())
        )
        with self._store.read("egress_newest_failure") as conn:
            row = next((r for r in conn.execute(stmt) if r.kind == _FAILED), None)
        return EgressFailureRecord(row.recorded_at, row.message) if row is not None else None

    def usage_after(self, position: UsagePosition, until: datetime, limit: int) -> Sequence[UsageRow]:
        u = s.usage_facts.c
        past = or_(
            u.recorded_at > position.recorded_at, and_(u.recorded_at == position.recorded_at, u.id > position.usage_id)
        )
        stmt = select(s.usage_facts).where(past, u.recorded_at <= until).order_by(u.recorded_at, u.id).limit(limit)
        with self._store.read("egress_usage_after") as conn:
            rows = conn.execute(stmt).all()
        return [UsageRow(r.id, r.chunk_id, r.runner_id, _fact(r)) for r in rows]

    def newest_egress_latch(self) -> EventLogKind | None:
        """Walks the event log newest-first to the first match — run once per process start, not per pass."""
        c = s.event_log.c
        stmt = select(c.kind).where(c.kind.in_(_LATCH_KINDS)).order_by(c.recorded_at.desc(), c.id.desc()).limit(1)
        with self._store.read("egress_newest_latch") as conn:
            kind = conn.execute(stmt).scalar_one_or_none()
        return next((k for k in _LATCH_KINDS if k == kind), None)

    def append_cursor(self, record: EgressCursorRecord) -> None:
        step = record.step
        with self._store.write("egress_append_cursor") as conn:
            conn.execute(
                insert(s.egress_cursor).values(
                    dataset=record.dataset,
                    position_at=step.at if step is not None else None,
                    chunk_id=step.chunk_id if step is not None else None,
                    epoch=step.epoch if step is not None else None,
                    decision_id=step.decision_id if step is not None else None,
                    usage_recorded_at=record.usage.recorded_at,
                    usage_id=record.usage.usage_id,
                    row_count=record.row_count,
                    files=json.dumps(list(record.files)),
                    recorded_at=record.recorded_at,
                )
            )


def _cursor(row) -> EgressCursorRecord:  # type: ignore[no-untyped-def]
    step = (
        CursorKey(row.position_at, row.chunk_id or "", row.epoch or 0, row.decision_id or "")
        if row.position_at is not None
        else None
    )
    return EgressCursorRecord(
        dataset=row.dataset,
        step=step,
        usage=UsagePosition(row.usage_recorded_at, row.usage_id),
        row_count=row.row_count,
        files=tuple(json.loads(row.files)),
        recorded_at=row.recorded_at,
    )


def _fact(u) -> UsageFact:  # type: ignore[no-untyped-def]
    return UsageFact(
        node_id=u.node_id,
        epoch=u.epoch,
        kind=u.kind,
        model=u.model,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_tokens,
        cache_create_tokens=u.cache_create_tokens,
        cost_usd=u.cost_usd,
        recorded_at=u.recorded_at,
        harness_id=u.harness_id,
        harness_version=u.harness_version,
        estimated_cost_usd=u.estimated_cost_usd,
    )


def _conforms_egress_store(x: EgressStore) -> IWriteEgressCursor:
    return x
