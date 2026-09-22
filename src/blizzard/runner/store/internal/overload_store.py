"""SQLAlchemy adapter for the provider-overload backoff repository seam (package-private,
blizzard#595)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, select

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.overload import InvocationKind, IWriteOverloadRepository, OverloadFactRecord
from blizzard.runner.store.internal.base import RunnerStoreConnections, Unclosed, Unsuperseded
from blizzard.runner.store.schema import lease_closures, overload_facts, overload_resets

_log = get_logger("blizzard.runner.store")

# A later overload fact on the same (lease, epoch) supersedes an earlier one.
_later_overload_facts = overload_facts.alias("later_overload_facts")
_NOT_SUPERSEDED_BY_LATER_OVERLOAD = Unsuperseded(
    _later_overload_facts.c.id,
    (
        _later_overload_facts.c.lease_id == overload_facts.c.lease_id,
        _later_overload_facts.c.epoch == overload_facts.c.epoch,
        _later_overload_facts.c.observed_at > overload_facts.c.observed_at,
    ),
)
# A tie (a clean exit's reset landing on the exact same clock reading as the very next
# overload) must side with the streak, not the reset — mirrors `overload_streak`'s own
# strict `observed_at > since`; a `>=` here would immediately re-close a fresh streak's
# own first fact.
_NOT_SUPERSEDED_BY_RESET = Unsuperseded(
    overload_resets.c.id,
    (
        overload_resets.c.lease_id == overload_facts.c.lease_id,
        overload_resets.c.epoch == overload_facts.c.epoch,
        overload_resets.c.reset_at > overload_facts.c.observed_at,
    ),
)
# `bzh:open-facts-declare-closure`: a hub-terminal chunk retires its lease (`lease_closures`)
# without ever writing a reset or a later overload, so a fact outliving its own lease closes
# here too — mirroring `ask_store.py`'s own anti-join rather than leaning on every caller
# scoping its read to `list_active_leases()`.
_NOT_SUPERSEDED_BY_LEASE_CLOSURE = Unclosed(overload_facts.c.lease_id, lease_closures.c.lease_id)


class OverloadStore:
    """Read-write provider-overload-backoff adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def overload_streak(self, lease_id: str, epoch: int) -> int:
        with self._store.connect() as conn:
            since = conn.execute(
                select(func.max(overload_resets.c.reset_at)).where(
                    and_(overload_resets.c.lease_id == lease_id, overload_resets.c.epoch == epoch)
                )
            ).scalar_one_or_none()
            conditions = [overload_facts.c.lease_id == lease_id, overload_facts.c.epoch == epoch]
            if since is not None:
                conditions.append(overload_facts.c.observed_at > since)
            return int(
                conn.execute(select(func.count()).select_from(overload_facts).where(and_(*conditions))).scalar_one()
            )

    def open_overload_facts(self) -> list[OverloadFactRecord]:
        rows = self._store.all(
            select(overload_facts).where(
                overload_facts.c.resume_after.is_not(None),
                _NOT_SUPERSEDED_BY_LATER_OVERLOAD.clause,
                _NOT_SUPERSEDED_BY_RESET.clause,
                _NOT_SUPERSEDED_BY_LEASE_CLOSURE.clause,
            )
        )
        return [self._row_to_record(r) for r in rows]

    def record_overload(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        epoch: int,
        generation: int,
        invocation_kind: InvocationKind,
        invocation_identity: str,
        streak_ordinal: int,
        observed_at: datetime,
        resume_after: datetime | None,
    ) -> None:
        # Check-then-insert, mirroring `nudge_facts` (`bzh:sql-portable`): re-classifying
        # the same exit on a later pass writes nothing.
        with self._store.begin() as conn:
            existing = conn.execute(
                select(overload_facts.c.id).where(
                    and_(
                        overload_facts.c.lease_id == lease_id,
                        overload_facts.c.epoch == epoch,
                        overload_facts.c.invocation_kind == invocation_kind,
                        overload_facts.c.invocation_identity == invocation_identity,
                    )
                )
            ).one_or_none()
            if existing is not None:
                return
            conn.execute(
                overload_facts.insert().values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    epoch=epoch,
                    generation=generation,
                    invocation_kind=invocation_kind,
                    invocation_identity=invocation_identity,
                    streak_ordinal=streak_ordinal,
                    observed_at=observed_at,
                    resume_after=resume_after,
                )
            )
        _log.info(
            "provider overload recorded",
            lease_id=lease_id,
            epoch=epoch,
            invocation_kind=invocation_kind,
            streak_ordinal=streak_ordinal,
            resume_after=iso_utc(resume_after) if resume_after else None,
        )

    def record_reset(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(overload_resets.insert().values(lease_id=lease_id, epoch=epoch, reset_at=at))
        _log.info("provider overload streak reset", lease_id=lease_id, epoch=epoch)

    @staticmethod
    def _row_to_record(r) -> OverloadFactRecord:  # type: ignore[no-untyped-def]
        return OverloadFactRecord(
            lease_id=str(r.lease_id),
            chunk_id=str(r.chunk_id),
            epoch=int(r.epoch),
            generation=int(r.generation),
            invocation_kind=r.invocation_kind,
            invocation_identity=str(r.invocation_identity),
            streak_ordinal=int(r.streak_ordinal),
            observed_at=r.observed_at,
            resume_after=r.resume_after,
        )


def _conforms_overload_store(x: OverloadStore) -> IWriteOverloadRepository:
    return x
