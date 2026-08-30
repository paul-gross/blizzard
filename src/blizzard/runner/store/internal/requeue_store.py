"""SQLAlchemy adapter for the requeue repository seam (package-private, blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.requeue import IWriteRequeueRepository
from blizzard.runner.store.internal.base import RunnerStoreConnections, Unsuperseded
from blizzard.runner.store.schema import leases, requeues

_log = get_logger("blizzard.runner.store")

# ``>=``: a mint at the mark's own instant is the spawn the mark itself triggered
# (pinned by tests/test_pin_runner_store.py::test_a_same_instant_mint_consumes_its_requeue_mark).
_UNCONSUMED_REQUEUE = Unsuperseded(
    leases.c.lease_id,
    (leases.c.chunk_id == requeues.c.chunk_id, leases.c.created_at >= requeues.c.requeued_at),
)


class RequeueStore:
    """Read-write requeue adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def pending_requeue_chunk_ids(self) -> set[str]:
        stmt = select(requeues.c.chunk_id).where(_UNCONSUMED_REQUEUE.clause).distinct()
        return {str(r.chunk_id) for r in self._store.all(stmt)}

    def record_requeue(self, *, chunk_id: str, at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(requeues.insert().values(chunk_id=chunk_id, requeued_at=at))
        _log.info("chunk requeued locally", chunk_id=chunk_id)


def _conforms_requeue_store(x: RequeueStore) -> IWriteRequeueRepository:
    return x
