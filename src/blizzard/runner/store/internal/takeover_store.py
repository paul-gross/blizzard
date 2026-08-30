"""SQLAlchemy adapter for the takeover repository seam (package-private, blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.domain.takeover import IWriteTakeoverRepository, TakeoverRecord
from blizzard.runner.store.internal.base import RunnerStoreConnections, Unclosed, lease_select, row_to_lease
from blizzard.runner.store.schema import leases, takeover_ends, takeovers

_log = get_logger("blizzard.runner.store")

_OPEN_TAKEOVER = Unclosed(takeovers.c.takeover_id, takeover_ends.c.takeover_id)


class TakeoverStore:
    """Read-write takeover adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def lease_for_open_takeover(self, lease_id: str) -> LeaseRecord | None:
        stmt = (
            lease_select()
            .join(takeovers, takeovers.c.lease_id == leases.c.lease_id)
            .where(leases.c.lease_id == lease_id)
            .where(_OPEN_TAKEOVER.clause)
        )
        rows = self._store.all(stmt)
        return row_to_lease(rows[0]) if rows else None

    def open_takeover_for_chunk(self, chunk_id: str) -> TakeoverRecord | None:
        stmt = (
            select(takeovers)
            .where(takeovers.c.chunk_id == chunk_id)
            .where(_OPEN_TAKEOVER.clause)
            .order_by(takeovers.c.opened_at.desc())
        )
        rows = self._store.all(stmt)
        return self._row_to_takeover(rows[0]) if rows else None

    def open_takeover_chunk_ids(self) -> set[str]:
        stmt = select(takeovers.c.chunk_id).where(_OPEN_TAKEOVER.clause).distinct()
        return {str(r.chunk_id) for r in self._store.all(stmt)}

    def open_takeovers(self) -> list[TakeoverRecord]:
        stmt = select(takeovers).where(_OPEN_TAKEOVER.clause).order_by(takeovers.c.opened_at.desc())
        return [self._row_to_takeover(r) for r in self._store.all(stmt)]

    def record_takeover(
        self,
        *,
        takeover_id: str,
        chunk_id: str,
        lease_id: str | None,
        session_id: str | None,
        workdir: str,
        fence_epoch: int | None,
        opened_at: datetime,
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                takeovers.insert().values(
                    takeover_id=takeover_id,
                    chunk_id=chunk_id,
                    lease_id=lease_id,
                    session_id=session_id,
                    workdir=workdir,
                    fence_epoch=fence_epoch,
                    opened_at=opened_at,
                )
            )
        _log.info("takeover opened", takeover_id=takeover_id, chunk_id=chunk_id, lease_id=lease_id, forced=fence_epoch)

    def record_takeover_end(self, *, takeover_id: str, ended_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(takeover_ends.insert().values(takeover_id=takeover_id, ended_at=ended_at))
        _log.info("takeover ended", takeover_id=takeover_id)

    @staticmethod
    def _row_to_takeover(r) -> TakeoverRecord:  # type: ignore[no-untyped-def]
        return TakeoverRecord(
            takeover_id=str(r.takeover_id),
            chunk_id=str(r.chunk_id),
            lease_id=str(r.lease_id) if r.lease_id is not None else None,
            session_id=str(r.session_id) if r.session_id is not None else None,
            workdir=str(r.workdir),
            fence_epoch=int(r.fence_epoch) if r.fence_epoch is not None else None,
            opened_at=r.opened_at,
        )


def _conforms_takeover_store(x: TakeoverStore) -> IWriteTakeoverRepository:
    return x
