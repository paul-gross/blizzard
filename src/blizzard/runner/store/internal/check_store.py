"""SQLAlchemy adapter for the check-result/nudge repository seam (package-private,
blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.checks import CheckResultRecord, IWriteCheckRepository
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import check_results, checks_ran, nudge_facts

_log = get_logger("blizzard.runner.store")


class CheckStore:
    """Read-write check-result/nudge adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def nudge_fired(self, lease_id: str, epoch: int) -> bool:
        rows = self._store.all(
            select(nudge_facts.c.lease_id).where(and_(nudge_facts.c.lease_id == lease_id, nudge_facts.c.epoch == epoch))
        )
        return bool(rows)

    def checks_ran(self, lease_id: str, epoch: int) -> bool:
        rows = self._store.all(
            select(checks_ran.c.id).where(and_(checks_ran.c.lease_id == lease_id, checks_ran.c.epoch == epoch))
        )
        return bool(rows)

    def check_results_for_lease(self, lease_id: str, epoch: int) -> list[CheckResultRecord]:
        # Ordered by insert id so the results read back in the order the checks ran.
        rows = self._store.all(
            select(check_results)
            .where(and_(check_results.c.lease_id == lease_id, check_results.c.epoch == epoch))
            .order_by(check_results.c.id)
        )
        return [
            CheckResultRecord(command=str(r.command), passed=bool(r.passed), output_tail=str(r.output_tail))
            for r in rows
        ]

    def record_nudge_fired(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        # Check-then-insert in one transaction, mirroring `record_usage` — idempotent by
        # construction rather than a DB constraint (`bzh:sql-portable`).
        with self._store.begin() as conn:
            existing = conn.execute(
                select(nudge_facts.c.id).where(and_(nudge_facts.c.lease_id == lease_id, nudge_facts.c.epoch == epoch))
            ).one_or_none()
            if existing is not None:
                return
            conn.execute(nudge_facts.insert().values(lease_id=lease_id, epoch=epoch, nudged_at=at))
        _log.info("nudge fired", lease_id=lease_id, epoch=epoch)

    def record_check_results(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        results: list[CheckResultRecord],
        at: datetime,
    ) -> None:
        # Delete-then-insert in one transaction, so a re-run for the same `(lease, epoch)`
        # is latest-wins. Written BEFORE `runner:checks-recorded-when-marked`'s marker.
        with self._store.begin() as conn:
            conn.execute(
                check_results.delete().where(and_(check_results.c.lease_id == lease_id, check_results.c.epoch == epoch))
            )
            for result in results:
                conn.execute(
                    check_results.insert().values(
                        lease_id=lease_id,
                        chunk_id=chunk_id,
                        node_id=node_id,
                        epoch=epoch,
                        command=result.command,
                        passed=result.passed,
                        output_tail=result.output_tail,
                        ran_at=at,
                    )
                )
        _log.info("check results recorded", lease_id=lease_id, epoch=epoch, count=len(results))

    def record_checks_ran(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        # Check-then-insert in one transaction — idempotent by construction, not by a DB
        # constraint (`bzh:sql-portable`). Written AFTER `runner:checks-recorded-when-marked`.
        with self._store.begin() as conn:
            existing = conn.execute(
                select(checks_ran.c.id).where(and_(checks_ran.c.lease_id == lease_id, checks_ran.c.epoch == epoch))
            ).one_or_none()
            if existing is not None:
                return
            conn.execute(checks_ran.insert().values(lease_id=lease_id, epoch=epoch, ran_at=at))
        _log.info("checks marked ran", lease_id=lease_id, epoch=epoch)


def _conforms_check_store(x: CheckStore) -> IWriteCheckRepository:
    return x
