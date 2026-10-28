"""SQLAlchemy adapter for the selftest-result repository seam."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.selftest_result import (
    IWriteSelfTestResultRepository,
    LatestSelfTestResult,
    SelfTestTerminalStatus,
)
from blizzard.runner.store.schema import selftest_results
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class SelfTestResultStore:
    """Read-write selftest-result adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def latest_selftest_result(self, harness_id: str) -> LatestSelfTestResult | None:
        # Ordered on the autoincrement pk, not `recorded_at` or insert order (`bzh:sql-portable`)
        # — mirrors `LeaseSessionStore.session_preamble_fingerprint`'s own latest-row read.
        rows = self._store.all(
            self._store.select(
                selftest_results,
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
        return LatestSelfTestResult(
            harness_id=harness_id,
            # The column holds only what `record_selftest_result` wrote, typed terminal on the way in.
            status=cast(SelfTestTerminalStatus, str(row.status)),
            error=str(row.error) if row.error is not None else None,
            recorded_at=row.recorded_at,
        )

    def record_selftest_result(
        self,
        *,
        harness_id: str,
        status: SelfTestTerminalStatus,
        error: str | None,
        recorded_at: datetime,
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                self._store.insert(selftest_results).values(
                    harness_id=harness_id,
                    status=status,
                    error=error,
                    recorded_at=recorded_at,
                )
            )
        _log.info("selftest result recorded", harness_id=harness_id, status=status)


_selftest_result_store_conforms: type[IWriteSelfTestResultRepository] = SelfTestResultStore
