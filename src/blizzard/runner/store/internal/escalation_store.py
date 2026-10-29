"""SQLAlchemy adapter for the escalation repository seam (package-private)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Row, Select

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.batching import id_batches
from blizzard.runner.leases.closure import ESCALATION_REASONS, cause_of
from blizzard.runner.leases.escalations import EscalationClosure, IWriteEscalationRepository, ParkedEscalation
from blizzard.runner.store.internal.base import LIVE_ESCALATION, UNRESOLVED_ESCALATION, Unsuperseded
from blizzard.runner.store.schema import escalation_closures, lease_closures, lease_context, lease_spawns, leases
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")

# A later closure of the same chunk, in append order, supersedes an earlier one.
_LATER_CLOSURE = escalation_closures.alias("later_escalation_closures")
_NEWEST_ESCALATION_CLOSURE = Unsuperseded(
    _LATER_CLOSURE.c.id,
    (_LATER_CLOSURE.c.chunk_id == escalation_closures.c.chunk_id, _LATER_CLOSURE.c.id > escalation_closures.c.id),
)


class EscalationStore:
    """Read-write escalation adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def open_escalations(self) -> list[ParkedEscalation]:
        stmt = (
            self._escalation_select()
            .where(LIVE_ESCALATION.clause(self._store))
            .where(UNRESOLVED_ESCALATION.clause(self._store))
            .order_by(lease_closures.c.closed_at.desc())
        )
        return [self._row_to_escalation(r) for r in self._store.all(stmt)]

    def closed_escalations(self, limit: int) -> list[ParkedEscalation]:
        # Each chunk's newest closure, kept in SQL (`bzh:newest-per-key-read`), joined to the
        # escalation it closed — the strict `>` mirrors `UNRESOLVED_ESCALATION`.
        stmt = (
            self._escalation_select(closed=True)
            .where(LIVE_ESCALATION.clause(self._store))
            .where(_NEWEST_ESCALATION_CLOSURE.clause(self._store))
            .order_by(escalation_closures.c.closed_at.desc(), escalation_closures.c.id.desc())
            .limit(limit)
        )
        return [self._row_to_escalation(r) for r in self._store.all(stmt)]

    def open_escalation_for_chunk(self, chunk_id: str) -> ParkedEscalation | None:
        return self.open_escalations_for([chunk_id]).get(chunk_id)

    def open_escalations_for(self, chunk_ids: Iterable[str]) -> dict[str, ParkedEscalation]:
        keys = sorted(set(chunk_ids))
        if not keys:
            return {}
        result: dict[str, ParkedEscalation] = {}
        with self._store.connect() as conn:
            for batch in id_batches(keys):
                # `LIVE_ESCALATION` keeps only the chunk's highest-epoch lease, and a lease closes
                # once, so each chunk's newest bound is already in SQL (`bzh:newest-per-key-read`).
                stmt = (
                    self._escalation_select()
                    .where(lease_closures.c.chunk_id.in_(batch))
                    .where(LIVE_ESCALATION.clause(self._store))
                    .where(UNRESOLVED_ESCALATION.clause(self._store))
                )
                for r in conn.execute(stmt):
                    result[str(r.chunk_id)] = self._row_to_escalation(r)
        return result

    def record_escalation_closure(self, *, chunk_id: str, reason: str, at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(self._store.insert(escalation_closures).values(chunk_id=chunk_id, reason=reason, closed_at=at))
        _log.info("escalation closed by the hub", chunk_id=chunk_id, reason=reason)

    def _escalation_select(self, *, closed: bool = False) -> Select[Any]:
        st = self._store
        # The lease's current generation's own recorded version — `id`
        # orders "newest generation" here exactly as the liveness store's own
        # `latest_spawn_harness_version` does: insertion order, not `spawned_at`.
        latest_harness_version = (
            st.select(lease_spawns, lease_spawns.c.harness_version)
            .where(lease_spawns.c.lease_id == leases.c.lease_id)
            .order_by(lease_spawns.c.id.desc())
            .limit(1)
            .correlate(leases)
            .scalar_subquery()
        )
        escalated = sa.join(lease_closures, leases, leases.c.lease_id == lease_closures.c.lease_id).join(
            lease_context, lease_context.c.lease_id == leases.c.lease_id
        )
        closure_columns: tuple[Any, ...] = (sa.null().label("closure_reason"), sa.null().label("closure_closed_at"))
        if closed:
            escalated = escalated.join(
                escalation_closures,
                sa.and_(
                    escalation_closures.c.chunk_id == lease_closures.c.chunk_id,
                    escalation_closures.c.closed_at > lease_closures.c.closed_at,
                ),
            )
            closure_columns = (
                escalation_closures.c.reason.label("closure_reason"),
                escalation_closures.c.closed_at.label("closure_closed_at"),
            )
        return st.select(
            escalated,
            lease_closures.c.lease_id,
            lease_closures.c.chunk_id,
            lease_closures.c.node_id,
            lease_closures.c.closed_at,
            lease_closures.c.reason,
            lease_closures.c.escalation_cause,
            leases.c.epoch,
            leases.c.session_id,
            leases.c.harness_id,
            # The escalated lease's session stamps — joined here rather
            # than read back per row.
            lease_context.c.session_name,
            lease_context.c.resolved_model,
            lease_context.c.resolved_effort,
            latest_harness_version.label("harness_version"),
            *closure_columns,
        ).where(lease_closures.c.reason.in_(sorted(ESCALATION_REASONS)))

    @staticmethod
    def _row_to_escalation(r: Row[Any]) -> ParkedEscalation:
        return ParkedEscalation(
            lease_id=str(r.lease_id),
            chunk_id=str(r.chunk_id),
            node_id=str(r.node_id),
            epoch=int(r.epoch),
            session_id=str(r.session_id) if r.session_id is not None else None,
            harness_id=str(r.harness_id) if r.harness_id is not None else None,
            closed_at=r.closed_at,
            session_name=r.session_name,
            resolved_model=r.resolved_model,
            resolved_effort=r.resolved_effort,
            harness_version=r.harness_version,
            cause=cause_of(str(r.reason), r.escalation_cause),
            closure=EscalationClosure(reason=str(r.closure_reason), closed_at=r.closure_closed_at)
            if r.closure_closed_at is not None
            else None,
        )


_escalation_store_conforms: type[IWriteEscalationRepository] = EscalationStore
