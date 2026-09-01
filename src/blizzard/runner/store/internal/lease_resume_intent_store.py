"""SQLAlchemy adapter for the lease resume-intent repository seam (package-private).

The restart resume-intent mark and its clear — issue #12/#13."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.leases import IWriteLeaseResumeIntentRepository
from blizzard.runner.store.internal.base import OPEN_INTENT, RunnerStoreConnections
from blizzard.runner.store.schema import resume_clears, resume_intents

_log = get_logger("blizzard.runner.store")


class LeaseResumeIntentStore:
    """Read-write resume-intent adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    # --- reads --------------------------------------------------------------

    def resume_intent_lease_ids(self) -> set[str]:
        stmt = select(resume_intents.c.lease_id).where(OPEN_INTENT.clause).distinct()
        return {str(r.lease_id) for r in self._store.all(stmt)}

    # --- writes -------------------------------------------------------------

    def record_resume_intent(self, *, lease_id: str, marked_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(resume_intents.insert().values(lease_id=lease_id, marked_at=marked_at))
        _log.info("resume intent marked", lease_id=lease_id)

    def record_resume_clear(self, *, lease_id: str, cleared_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(resume_clears.insert().values(lease_id=lease_id, cleared_at=cleared_at))
        _log.info("resume intent cleared", lease_id=lease_id)


def _conforms_lease_resume_intent_store(x: LeaseResumeIntentStore) -> IWriteLeaseResumeIntentRepository:
    return x
