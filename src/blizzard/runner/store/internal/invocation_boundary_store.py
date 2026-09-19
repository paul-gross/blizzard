"""SQLAlchemy adapter for the invocation-boundary repository seam (package-private,
blizzard#437 D6/D11)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.invocation_boundaries import (
    InvocationBoundaryKind,
    InvocationBoundaryRecord,
    IWriteInvocationBoundaryRepository,
)
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import invocation_boundaries

_log = get_logger("blizzard.runner.store")


def _record(r: object) -> InvocationBoundaryRecord:
    return InvocationBoundaryRecord(
        lease_id=str(r.lease_id),  # type: ignore[attr-defined]
        chunk_id=str(r.chunk_id),  # type: ignore[attr-defined]
        node_id=str(r.node_id),  # type: ignore[attr-defined]
        epoch=int(r.epoch),  # type: ignore[attr-defined]
        generation=int(r.generation),  # type: ignore[attr-defined]
        kind=r.kind,  # type: ignore[attr-defined]
        start_position=str(r.start_position) if r.start_position is not None else None,  # type: ignore[attr-defined]
        opened_at=r.opened_at,  # type: ignore[attr-defined]
        closed_at=r.closed_at,  # type: ignore[attr-defined]
        closed_reason=str(r.closed_reason) if r.closed_reason is not None else None,  # type: ignore[attr-defined]
    )


class InvocationBoundaryStore:
    """Read-write invocation-boundary adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def boundary(self, lease_id: str, generation: int, kind: InvocationBoundaryKind) -> InvocationBoundaryRecord | None:
        rows = self._store.all(
            select(invocation_boundaries).where(
                and_(
                    invocation_boundaries.c.lease_id == lease_id,
                    invocation_boundaries.c.generation == generation,
                    invocation_boundaries.c.kind == kind,
                )
            )
        )
        if not rows:
            return None
        return _record(rows[0])

    def open_boundaries_for_lease(self, lease_id: str) -> list[InvocationBoundaryRecord]:
        rows = self._store.all(
            select(invocation_boundaries)
            .where(and_(invocation_boundaries.c.lease_id == lease_id, invocation_boundaries.c.closed_at.is_(None)))
            .order_by(invocation_boundaries.c.opened_at, invocation_boundaries.c.id)
        )
        return [_record(r) for r in rows]

    def record_boundary_open(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        kind: InvocationBoundaryKind,
        start_position: str | None,
        opened_at: datetime,
    ) -> None:
        # Check-then-insert in one transaction, mirroring `record_usage` — idempotent by
        # construction rather than a DB constraint (`bzh:sql-portable`).
        with self._store.begin() as conn:
            existing = conn.execute(
                select(invocation_boundaries.c.id).where(
                    and_(
                        invocation_boundaries.c.lease_id == lease_id,
                        invocation_boundaries.c.generation == generation,
                        invocation_boundaries.c.kind == kind,
                    )
                )
            ).one_or_none()
            if existing is not None:
                return
            conn.execute(
                invocation_boundaries.insert().values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    generation=generation,
                    kind=kind,
                    start_position=start_position,
                    opened_at=opened_at,
                    closed_at=None,
                    closed_reason=None,
                )
            )
        _log.info("invocation boundary opened", lease_id=lease_id, generation=generation, kind=kind, chunk_id=chunk_id)

    def close_boundaries_for_lease(self, lease_id: str, *, reason: str, at: datetime) -> None:
        # An UPDATE over `closed_at IS NULL` — naturally idempotent under a crash-and-retry
        # of the closure path that calls it (`Attempt.close`).
        with self._store.begin() as conn:
            conn.execute(
                invocation_boundaries.update()
                .where(and_(invocation_boundaries.c.lease_id == lease_id, invocation_boundaries.c.closed_at.is_(None)))
                .values(closed_at=at, closed_reason=reason)
            )
        _log.info("invocation boundaries closed", lease_id=lease_id, reason=reason)


def _conforms_invocation_boundary_store(x: InvocationBoundaryStore) -> IWriteInvocationBoundaryRepository:
    return x
