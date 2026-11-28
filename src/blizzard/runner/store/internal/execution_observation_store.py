"""SQLAlchemy adapter for the execution-observation repository seam (package-private)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_

from blizzard.foundation.instants import as_utc
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.batching import id_batches
from blizzard.runner.harness.observation import ExecutionObservation
from blizzard.runner.leases.execution_observation import (
    ExecutionObservationState,
    IWriteExecutionObservationRepository,
    WarnedStall,
)
from blizzard.runner.store.internal.base import insert_outbox_row
from blizzard.runner.store.schema import execution_observations, outbound_buffer
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")

# See IWriteExecutionObservationRepository.prune_execution_observations's own docstring for the contract.
_EXECUTION_OBSERVATION_RETENTION_WINDOW = timedelta(days=1)


class ExecutionObservationStore:
    """Read-write execution-observation adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def execution_observation_states(self, lease_ids: Sequence[str]) -> dict[str, ExecutionObservationState]:
        if not lease_ids:
            return {}
        table = execution_observations
        observed: dict[str, tuple[datetime, datetime | None]] = {}
        warned: dict[str, WarnedStall] = {}
        with self._store.connect() as conn:
            for batch in id_batches(lease_ids):
                stmt = (
                    self._store.select(
                        table,
                        table.c.lease_id,
                        func.max(table.c.observed_at).label("last_observed_at"),
                        func.max(table.c.progress_at).label("latest_progress_at"),
                    )
                    .where(table.c.lease_id.in_(batch))
                    .group_by(table.c.lease_id)
                )
                for row in conn.execute(stmt):
                    observed[str(row.lease_id)] = (row.last_observed_at, row.latest_progress_at)
                # The newest warning row per lease, by insertion order (`bzh:newest-per-key-read`).
                newest = (
                    self._store.select(table, func.max(table.c.id).label("id"))
                    .where(table.c.lease_id.in_(batch), table.c.warned_anchor_at.isnot(None))
                    .group_by(table.c.lease_id)
                    .subquery()
                )
                warned_stmt = self._store.select(
                    table, table.c.lease_id, table.c.generation, table.c.warned_anchor_at
                ).join(newest, table.c.id == newest.c.id)
                for row in conn.execute(warned_stmt):
                    warned[str(row.lease_id)] = WarnedStall(
                        generation=int(row.generation), anchor_at=as_utc(row.warned_anchor_at)
                    )
        return {
            lease_id: ExecutionObservationState(
                last_observed_at=as_utc(last_observed_at),
                latest_progress_at=as_utc(progress_at) if progress_at is not None else None,
                warned=warned.get(lease_id),
            )
            for lease_id, (last_observed_at, progress_at) in observed.items()
        }

    def record_execution_observation(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        generation: int,
        observation: ExecutionObservation,
        observed_at: datetime,
        warned_anchor_at: datetime | None = None,
        report_kind: str = "",
        report_payload: str = "",
    ) -> int | None:
        # The row and its warning land in ONE transaction: the row is what the dedupe reads.
        seq: int | None = None
        with self._store.begin() as conn:
            conn.execute(
                self._store.insert(execution_observations).values(
                    lease_id=lease_id,
                    generation=generation,
                    observed_at=observed_at,
                    liveness=observation.liveness.value,
                    activity=observation.activity.value,
                    evidence_at=observation.evidence_at,
                    progress_at=observation.progress_at,
                    diagnostic=observation.diagnostic,
                    warned_anchor_at=warned_anchor_at,
                )
            )
            if report_kind:
                seq = insert_outbox_row(
                    self._store,
                    conn,
                    outbound_buffer,
                    kind=report_kind,
                    chunk_id=chunk_id,
                    lease_id=lease_id,
                    payload=report_payload,
                    created_at=observed_at,
                )
        if report_kind:
            _log.warning(
                "worker made no progress past the warn threshold",
                lease_id=lease_id,
                generation=generation,
                activity=observation.activity.value,
            )
        return seq

    def prune_execution_observations(self, *, now: datetime) -> int:
        cutoff = now - _EXECUTION_OBSERVATION_RETENTION_WINDOW
        table = execution_observations
        newest_row = table.alias("newest_row")
        newest_id = (
            self._store.select(newest_row, func.max(newest_row.c.id))
            .where(newest_row.c.lease_id == table.c.lease_id)
            .scalar_subquery()
        )
        newest_warning = table.alias("newest_warning")
        newest_warning_id = (
            self._store.select(newest_warning, func.max(newest_warning.c.id))
            .where(newest_warning.c.lease_id == table.c.lease_id, newest_warning.c.warned_anchor_at.isnot(None))
            .scalar_subquery()
        )
        with self._store.begin() as conn:
            result = conn.execute(
                self._store.delete(table).where(
                    and_(
                        table.c.observed_at < cutoff,
                        table.c.id < newest_id,
                        or_(table.c.warned_anchor_at.is_(None), table.c.id < newest_warning_id),
                    )
                )
            )
        return result.rowcount


_execution_observation_store_conforms: type[IWriteExecutionObservationRepository] = ExecutionObservationStore
