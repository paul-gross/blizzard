"""SQLAlchemy adapter for the selftest-result repository seam (blizzard#438)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.selftest_result import IWriteSelfTestResultRepository, SelfTestResultRecord
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import selftest_results

_log = get_logger("blizzard.runner.store")


class SelfTestResultStore:
    """Read-write selftest-result adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def latest_selftest_result(self, harness_id: str) -> SelfTestResultRecord | None:
        # Ordered on the autoincrement pk, not `recorded_at` or insert order (`bzh:sql-portable`)
        # — mirrors `LeaseSessionStore.session_preamble_fingerprint`'s own latest-row read.
        rows = self._store.all(
            select(
                selftest_results.c.status,
                selftest_results.c.error,
                selftest_results.c.recorded_at,
            )
            .where(selftest_results.c.harness_id == harness_id)
            .order_by(selftest_results.c.id.desc())
            .limit(1)
        )
        if not rows:
            return None
        row = rows[0]
        return SelfTestResultRecord(
            harness_id=harness_id,
            status=str(row.status),
            error=str(row.error) if row.error is not None else None,
            recorded_at=row.recorded_at,
        )

    def record_selftest_result(
        self,
        *,
        harness_id: str,
        status: str,
        error: str | None,
        recorded_at: datetime,
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                selftest_results.insert().values(
                    harness_id=harness_id,
                    status=status,
                    error=error,
                    recorded_at=recorded_at,
                )
            )
        _log.info("selftest result recorded", harness_id=harness_id, status=status)


def _conforms_selftest_result_store(x: SelfTestResultStore) -> IWriteSelfTestResultRepository:
    return x
