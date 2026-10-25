"""SQLAlchemy adapter for the takeover repository seam (package-private)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Row, Select, and_, or_

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.batching import id_batches
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.model import Lease
from blizzard.runner.lifecycle.takeover import IWriteTakeoverRepository, OpenTakeover
from blizzard.runner.store.internal.base import Unclosed, lease_select, row_to_lease
from blizzard.runner.store.schema import leases, takeover_ends, takeovers
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")

_OPEN_TAKEOVER = Unclosed(takeovers.c.takeover_id, takeover_ends.c.takeover_id)


class TakeoverStore:
    """Read-write takeover adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def lease_for_open_takeover(self, lease_id: str) -> Lease | None:
        stmt = (
            lease_select(self._store)
            .join(takeovers, takeovers.c.lease_id == leases.c.lease_id)
            .where(*self._store.key_predicates(takeovers))
            .where(leases.c.lease_id == lease_id)
            .where(_OPEN_TAKEOVER.clause(self._store))
        )
        rows = self._store.all(stmt)
        return row_to_lease(rows[0]) if rows else None

    def open_takeover_for_chunk(self, chunk_id: str) -> OpenTakeover | None:
        return self.open_takeovers_for([chunk_id]).get(chunk_id)

    def open_takeovers_for(self, chunk_ids: Iterable[str]) -> dict[str, OpenTakeover]:
        keys = sorted(set(chunk_ids))
        if not keys:
            return {}
        st = self._store
        result: dict[str, OpenTakeover] = {}
        with st.connect() as conn:
            for batch in id_batches(keys):
                stmt = (
                    self._takeover_select(st)
                    .where(takeovers.c.chunk_id.in_(batch))
                    .where(_OPEN_TAKEOVER.clause(st))
                    .where(~self._newer_open_takeover(st))
                )
                for r in conn.execute(stmt):
                    result[str(r.chunk_id)] = self._row_to_takeover(r)
        return result

    def open_takeover_chunk_ids(self) -> set[str]:
        stmt = self._store.select(takeovers, takeovers.c.chunk_id).where(_OPEN_TAKEOVER.clause(self._store)).distinct()
        return {str(r.chunk_id) for r in self._store.all(stmt)}

    def open_takeovers(self) -> list[OpenTakeover]:
        stmt = (
            self._takeover_select(self._store)
            .where(_OPEN_TAKEOVER.clause(self._store))
            .order_by(takeovers.c.opened_at.desc())
        )
        return [self._row_to_takeover(r) for r in self._store.all(stmt)]

    def record_takeover(
        self,
        *,
        takeover_id: str,
        chunk_id: str,
        lease_id: str | None,
        workdir: str,
        fence_epoch: int | None,
        opened_at: datetime,
        session: SessionReference,
        hold_epoch: int | None = None,
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                self._store.insert(takeovers).values(
                    takeover_id=takeover_id,
                    chunk_id=chunk_id,
                    lease_id=lease_id,
                    session_id=session.session_id,
                    harness_id=session.harness_id,
                    workdir=workdir,
                    fence_epoch=fence_epoch,
                    hold_epoch=hold_epoch,
                    opened_at=opened_at,
                )
            )
        _log.info("takeover opened", takeover_id=takeover_id, chunk_id=chunk_id, lease_id=lease_id, forced=fence_epoch)

    def record_takeover_end(self, *, takeover_id: str, ended_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(self._store.insert(takeover_ends).values(takeover_id=takeover_id, ended_at=ended_at))
        _log.info("takeover ended", takeover_id=takeover_id)

    @staticmethod
    def _newer_open_takeover(store: RunnerStoreConnections) -> ColumnElement[bool]:
        """``EXISTS`` a later open takeover of the outer row's chunk — negated, it keeps each chunk's
        newest open takeover in SQL (``bzh:newest-per-key-read``), ``opened_at`` then ``takeover_id``."""
        rival = takeovers.alias("newer_takeover")
        later = or_(
            rival.c.opened_at > takeovers.c.opened_at,
            and_(rival.c.opened_at == takeovers.c.opened_at, rival.c.takeover_id > takeovers.c.takeover_id),
        )
        still_open = rival.c.takeover_id.not_in(store.select(takeover_ends, takeover_ends.c.takeover_id))
        return store.exists(rival, rival.c.chunk_id == takeovers.c.chunk_id, later, still_open)

    @staticmethod
    def _takeover_select(store: RunnerStoreConnections) -> Select[Any]:
        # The reference lease's own epoch rides along, so the takeover's hold is scoped to it.
        return store.select(
            store.scope_join(takeovers, leases, leases.c.lease_id == takeovers.c.lease_id, isouter=True),
            takeovers,
            leases.c.epoch.label("reference_epoch"),
        )

    @staticmethod
    def _row_to_takeover(r: Row[Any]) -> OpenTakeover:
        return OpenTakeover(
            takeover_id=str(r.takeover_id),
            chunk_id=str(r.chunk_id),
            lease_id=str(r.lease_id) if r.lease_id is not None else None,
            session_id=str(r.session_id) if r.session_id is not None else None,
            workdir=str(r.workdir),
            fence_epoch=int(r.fence_epoch) if r.fence_epoch is not None else None,
            opened_at=r.opened_at,
            harness_id=str(r.harness_id) if r.harness_id is not None else None,
            reference_epoch=int(r.reference_epoch) if r.reference_epoch is not None else None,
            hold_epoch=int(r.hold_epoch) if r.hold_epoch is not None else None,
        )


def _conforms_takeover_store(x: TakeoverStore) -> IWriteTakeoverRepository:
    return x
