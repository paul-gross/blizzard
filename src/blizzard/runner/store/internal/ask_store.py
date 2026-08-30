"""SQLAlchemy adapter for the ask/park repository seam (package-private, blizzard#410).

:meth:`AskStore.parked_lease_ids` reaches into :class:`~blizzard.runner.store.internal.
pause_store.PauseStore` for its pause-park half — the union both concepts' Protocols agree
:meth:`~blizzard.runner.domain.asks.IReadAskRepository.parked_lease_ids` answers."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.asks import AskRecord, IWriteAskRepository, ParkRecord
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.internal.pause_store import PauseStore
from blizzard.runner.store.schema import asks, lease_closures, park_facts, park_resumes

_log = get_logger("blizzard.runner.store")


class AskStore:
    """Read-write ask/park adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def unforwarded_ask(self, lease_id: str) -> AskRecord | None:
        stmt = (
            select(asks)
            .where(asks.c.lease_id == lease_id)
            .where(asks.c.question_id.not_in(select(park_facts.c.question_id)))
            .order_by(asks.c.id.desc())
        )
        rows = self._store.all(stmt)
        return self._row_to_ask(rows[0]) if rows else None

    def parked_lease_ids(self) -> set[str]:
        return self.ask_parked_lease_ids() | PauseStore(self._store).pause_parked_lease_ids()

    def ask_parked_lease_ids(self) -> set[str]:
        stmt = select(park_facts.c.lease_id).where(park_facts.c.question_id.not_in(select(park_resumes.c.question_id)))
        return {str(r.lease_id) for r in self._store.all(stmt)}

    def open_park(self, lease_id: str) -> ParkRecord | None:
        stmt = (
            select(park_facts)
            .where(park_facts.c.lease_id == lease_id)
            .where(park_facts.c.question_id.not_in(select(park_resumes.c.question_id)))
            .order_by(park_facts.c.id.desc())
        )
        rows = self._store.all(stmt)
        if not rows:
            return None
        r = rows[0]
        return ParkRecord(
            lease_id=str(r.lease_id),
            chunk_id=str(r.chunk_id),
            question_id=str(r.question_id),
            parked_at=r.parked_at,
        )

    def open_asks(self) -> list[AskRecord]:
        # An ask whose lease has closed is never open — a backstop independent of which
        # path writes the retiring `park_resumes` row (blizzard#202).
        stmt = (
            select(asks)
            .where(asks.c.question_id.not_in(select(park_resumes.c.question_id)))
            .where(asks.c.lease_id.not_in(select(lease_closures.c.lease_id)))
            .order_by(asks.c.id.desc())
        )
        return [self._row_to_ask(r) for r in self._store.all(stmt)]

    def record_ask(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        question_id: str,
        question: str,
        options: list[str],
        session_id: str | None,
        asked_at: datetime,
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                asks.insert().values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    question_id=question_id,
                    question=question,
                    options=json.dumps(options),
                    session_id=session_id,
                    asked_at=asked_at,
                )
            )
        _log.info("ask recorded", lease_id=lease_id, chunk_id=chunk_id, question_id=question_id)

    def record_park(self, *, lease_id: str, chunk_id: str, question_id: str, parked_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(
                park_facts.insert().values(
                    lease_id=lease_id, chunk_id=chunk_id, question_id=question_id, parked_at=parked_at
                )
            )
        _log.info("chunk parked on question", lease_id=lease_id, chunk_id=chunk_id, question_id=question_id)

    def record_park_resume(self, *, lease_id: str, question_id: str, resumed_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(
                park_resumes.insert().values(lease_id=lease_id, question_id=question_id, resumed_at=resumed_at)
            )
        _log.info("park resumed with answer", lease_id=lease_id, question_id=question_id)

    @staticmethod
    def _row_to_ask(r) -> AskRecord:  # type: ignore[no-untyped-def]
        return AskRecord(
            lease_id=str(r.lease_id),
            chunk_id=str(r.chunk_id),
            question_id=str(r.question_id),
            question=str(r.question),
            options=json.loads(r.options) if r.options else [],
            session_id=str(r.session_id) if r.session_id is not None else None,
            asked_at=r.asked_at,
        )


def _conforms_ask_store(x: AskStore) -> IWriteAskRepository:
    return x
