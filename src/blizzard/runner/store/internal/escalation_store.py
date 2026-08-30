"""SQLAlchemy adapter for the escalation repository seam (package-private, blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.escalations import EscalationRecord, IWriteEscalationRepository
from blizzard.runner.store.internal.base import LIVE_ESCALATION, UNRESOLVED_ESCALATION, RunnerStoreConnections
from blizzard.runner.store.schema import escalation_closures, lease_closures, lease_context, leases

_log = get_logger("blizzard.runner.store")

# The caller-owned closure reason this store reads back to derive "open escalation"
# (issue #51).
_ESCALATED_REASON = "escalated"


class EscalationStore:
    """Read-write escalation adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def open_escalations(self) -> list[EscalationRecord]:
        stmt = (
            self._escalation_select()
            .where(LIVE_ESCALATION.clause)
            .where(UNRESOLVED_ESCALATION.clause)
            .order_by(lease_closures.c.closed_at.desc())
        )
        return [self._row_to_escalation(r) for r in self._store.all(stmt)]

    def open_escalation_for_chunk(self, chunk_id: str) -> EscalationRecord | None:
        stmt = (
            self._escalation_select()
            .where(lease_closures.c.chunk_id == chunk_id)
            .where(LIVE_ESCALATION.clause)
            .where(UNRESOLVED_ESCALATION.clause)
            .order_by(lease_closures.c.closed_at.desc())
        )
        rows = self._store.all(stmt)
        return self._row_to_escalation(rows[0]) if rows else None

    def record_escalation_closure(self, *, chunk_id: str, reason: str, at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(escalation_closures.insert().values(chunk_id=chunk_id, reason=reason, closed_at=at))
        _log.info("escalation closed by the hub", chunk_id=chunk_id, reason=reason)

    @staticmethod
    def _escalation_select():  # type: ignore[no-untyped-def]
        return (
            select(
                lease_closures.c.lease_id,
                lease_closures.c.chunk_id,
                lease_closures.c.node_id,
                lease_closures.c.closed_at,
                leases.c.epoch,
                leases.c.session_id,
                # The escalated lease's session stamps (issue #144) — joined here rather
                # than read back per row.
                lease_context.c.session_name,
                lease_context.c.resolved_model,
                lease_context.c.resolved_effort,
            )
            .select_from(
                lease_closures.join(leases, leases.c.lease_id == lease_closures.c.lease_id).join(
                    lease_context, lease_context.c.lease_id == leases.c.lease_id
                )
            )
            .where(lease_closures.c.reason == _ESCALATED_REASON)
        )

    @staticmethod
    def _row_to_escalation(r) -> EscalationRecord:  # type: ignore[no-untyped-def]
        return EscalationRecord(
            lease_id=str(r.lease_id),
            chunk_id=str(r.chunk_id),
            node_id=str(r.node_id),
            epoch=int(r.epoch),
            session_id=str(r.session_id) if r.session_id is not None else None,
            closed_at=r.closed_at,
            session_name=r.session_name,
            resolved_model=r.resolved_model,
            resolved_effort=r.resolved_effort,
        )


def _conforms_escalation_store(x: EscalationStore) -> IWriteEscalationRepository:
    return x
