"""SQLAlchemy adapter for the ask/park repository seam (package-private).

Pause-park lease ids come from the shared ``base.pause_parked_lease_ids`` query."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Row

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.asks import IWriteAskRepository, OpenAsk, QuestionPark, newest_unforwarded, open_asks_of
from blizzard.runner.store.internal.base import pause_parked_lease_ids
from blizzard.runner.store.schema import asks, lease_closures, park_facts, park_resumes
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class AskStore:
    """Read-write ask/park adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def unforwarded_ask(self, lease_id: str) -> OpenAsk | None:
        st = self._store
        stmt = st.select(asks).where(asks.c.lease_id == lease_id).order_by(asks.c.id.desc()).limit(1)
        newest = [self._row_to_ask(r) for r in self._store.all(stmt)]
        if not newest:
            return None
        parked = st.select(park_facts, park_facts.c.question_id).where(
            park_facts.c.question_id == newest[0].question_id
        )
        return newest_unforwarded(newest, forwarded={str(r.question_id) for r in self._store.all(parked)})

    def parked_lease_ids(self) -> set[str]:
        pause_parked = {str(r.lease_id) for r in self._store.all(pause_parked_lease_ids(self._store))}
        return self.ask_parked_lease_ids() | pause_parked

    def ask_parked_lease_ids(self) -> set[str]:
        st = self._store
        stmt = st.select(park_facts, park_facts.c.lease_id).where(
            park_facts.c.question_id.not_in(st.select(park_resumes, park_resumes.c.question_id))
        )
        return {str(r.lease_id) for r in self._store.all(stmt)}

    def open_park(self, lease_id: str) -> QuestionPark | None:
        st = self._store
        stmt = (
            st.select(park_facts)
            .where(park_facts.c.lease_id == lease_id)
            .where(park_facts.c.question_id.not_in(st.select(park_resumes, park_resumes.c.question_id)))
            .order_by(park_facts.c.id.desc())
            .limit(1)
        )
        rows = self._store.all(stmt)
        if not rows:
            return None
        r = rows[0]
        return QuestionPark(
            lease_id=str(r.lease_id),
            chunk_id=str(r.chunk_id),
            question_id=str(r.question_id),
            parked_at=r.parked_at,
        )

    def open_asks(self) -> list[OpenAsk]:
        # An ask whose lease has closed is never open — a backstop independent of which
        # path writes the retiring `park_resumes` row.
        st = self._store
        live = asks.c.lease_id.not_in(st.select(lease_closures, lease_closures.c.lease_id))
        stmt = st.select(asks).where(live).order_by(asks.c.id.desc())
        # Park rows are read only for the candidate asks, so the cost tracks the live fleet, not the
        # deployment's history.
        candidates = st.select(asks, asks.c.question_id).where(live)
        forwarded_stmt = st.select(park_facts, park_facts.c.question_id).where(park_facts.c.question_id.in_(candidates))
        answered_stmt = st.select(park_resumes, park_resumes.c.question_id).where(
            park_resumes.c.question_id.in_(candidates)
        )
        forwarded = {str(r.question_id) for r in self._store.all(forwarded_stmt)}
        answered = {str(r.question_id) for r in self._store.all(answered_stmt)}
        return open_asks_of(
            [self._row_to_ask(r) for r in self._store.all(stmt)], forwarded=forwarded, answered=answered
        )

    def record_ask(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        question_id: str,
        question: str,
        options: list[str],
        asked_at: datetime,
        session: SessionReference | None = None,
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                self._store.insert(asks).values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    question_id=question_id,
                    question=question,
                    options=json.dumps(options),
                    session_id=session.session_id if session is not None else None,
                    harness_id=session.harness_id if session is not None else None,
                    asked_at=asked_at,
                )
            )
        _log.info("ask recorded", lease_id=lease_id, chunk_id=chunk_id, question_id=question_id)

    def record_park(self, *, lease_id: str, chunk_id: str, question_id: str, parked_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(
                self._store.insert(park_facts).values(
                    lease_id=lease_id, chunk_id=chunk_id, question_id=question_id, parked_at=parked_at
                )
            )
        _log.info("chunk parked on question", lease_id=lease_id, chunk_id=chunk_id, question_id=question_id)

    def record_park_resume(self, *, lease_id: str, question_id: str, resumed_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(
                self._store.insert(park_resumes).values(
                    lease_id=lease_id, question_id=question_id, resumed_at=resumed_at
                )
            )
        _log.info("park resumed with answer", lease_id=lease_id, question_id=question_id)

    @staticmethod
    def _row_to_ask(r: Row[Any]) -> OpenAsk:
        return OpenAsk(
            lease_id=str(r.lease_id),
            chunk_id=str(r.chunk_id),
            question_id=str(r.question_id),
            question=str(r.question),
            options=json.loads(r.options) if r.options else [],
            session_id=str(r.session_id) if r.session_id is not None else None,
            asked_at=r.asked_at,
            harness_id=str(r.harness_id) if r.harness_id is not None else None,
        )


_ask_store_conforms: type[IWriteAskRepository] = AskStore
