"""SQLAlchemy adapter for the requeue repository seam (package-private)."""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.logging import get_logger
from blizzard.runner.leases.operator_requests import IWriteRequeueRepository
from blizzard.runner.store.internal.base import Unsuperseded, insert_fact_row
from blizzard.runner.store.schema import leases, requeues
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")

# A mint consumes only a mark appended before it: the spawn the mark itself triggered is minted after it, at whatever
# instant (pinned by tests/test_pin_runner_store.py::test_a_same_instant_mint_consumes_its_requeue_mark).
_UNCONSUMED_REQUEUE = Unsuperseded(
    leases.c.lease_id,
    (leases.c.chunk_id == requeues.c.chunk_id, leases.c.seq > requeues.c.seq),
)


class RequeueStore:
    """Read-write requeue adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def pending_requeue_chunk_ids(self) -> set[str]:
        stmt = (
            self._store.select(requeues, requeues.c.chunk_id).where(_UNCONSUMED_REQUEUE.clause(self._store)).distinct()
        )
        return {str(r.chunk_id) for r in self._store.all(stmt)}

    def record_requeue(self, *, chunk_id: str, at: datetime) -> None:
        with self._store.begin() as conn:
            insert_fact_row(self._store, conn, requeues, chunk_id=chunk_id, requeued_at=at)
        _log.info("chunk requeued locally", chunk_id=chunk_id)


def _conforms_requeue_store(x: RequeueStore) -> IWriteRequeueRepository:
    return x
