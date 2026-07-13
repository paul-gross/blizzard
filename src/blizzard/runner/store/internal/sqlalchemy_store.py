"""SQLAlchemy adapter for the runner-store repository (package-private).

The one place the reconciliation loop's facts touch the engine (``bzh:pluggable-seams``).
All library usage is confined here; a driver failure is wrapped once into
:class:`~blizzard.runner.store.repository.RunnerStoreError` (logged at the wrap
site, ``bzh:structlog-logging``) so loop code never depends on SQLAlchemy's
exceptions. Every derived query realizes the facts-only invariant in SQL:
active = no closure, held = no release (``bzh:facts-not-status``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, and_, func, select
from sqlalchemy.exc import SQLAlchemyError

from blizzard.foundation.logging import get_logger
from blizzard.runner.store.repository import (
    BufferedFact,
    EnvBindingRecord,
    IWriteRunnerStore,
    LeaseRecord,
    NewLease,
    RunnerStoreError,
)
from blizzard.runner.store.schema import (
    binding_releases,
    env_bindings,
    lease_closures,
    lease_context,
    leases,
    outbound_buffer,
)

_log = get_logger("blizzard.runner.store")


class SqlAlchemyRunnerStore:
    """Read-write runner store over a SQLAlchemy engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- reads --------------------------------------------------------------

    def list_active_leases(self) -> list[LeaseRecord]:
        stmt = self._lease_select().where(leases.c.lease_id.not_in(select(lease_closures.c.lease_id)))
        return [self._row_to_lease(r) for r in self._all(stmt)]

    def active_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        stmt = (
            self._lease_select()
            .where(leases.c.chunk_id == chunk_id)
            .where(leases.c.lease_id.not_in(select(lease_closures.c.lease_id)))
            .order_by(leases.c.created_at.desc())
        )
        rows = self._all(stmt)
        return self._row_to_lease(rows[0]) if rows else None

    def held_environment_ids(self) -> list[str]:
        stmt = select(env_bindings.c.environment_id).where(
            env_bindings.c.environment_id.not_in(
                select(binding_releases.c.environment_id).where(binding_releases.c.chunk_id == env_bindings.c.chunk_id)
            )
        )
        return [str(r.environment_id) for r in self._all(stmt)]

    def bindings_for_chunk(self, chunk_id: str) -> list[EnvBindingRecord]:
        stmt = (
            select(env_bindings)
            .where(env_bindings.c.chunk_id == chunk_id)
            .where(
                env_bindings.c.environment_id.not_in(
                    select(binding_releases.c.environment_id).where(binding_releases.c.chunk_id == chunk_id)
                )
            )
            .order_by(env_bindings.c.bound_at)
        )
        return [
            EnvBindingRecord(
                chunk_id=str(r.chunk_id),
                environment_id=str(r.environment_id),
                workdir=str(r.workdir),
                bound_at=r.bound_at,
            )
            for r in self._all(stmt)
        ]

    def live_tenure_chunk_ids(self) -> list[str]:
        stmt = (
            select(env_bindings.c.chunk_id)
            .where(
                env_bindings.c.environment_id.not_in(
                    select(binding_releases.c.environment_id).where(
                        binding_releases.c.chunk_id == env_bindings.c.chunk_id
                    )
                )
            )
            .distinct()
        )
        return [str(r.chunk_id) for r in self._all(stmt)]

    def attempt_count(self, chunk_id: str, node_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(lease_context)
            .where(and_(lease_context.c.chunk_id == chunk_id, lease_context.c.node_id == node_id))
        )
        with self._connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def latest_epoch(self, chunk_id: str) -> int:
        stmt = select(func.max(leases.c.epoch)).where(leases.c.chunk_id == chunk_id)
        with self._connect() as conn:
            value = conn.execute(stmt).scalar_one_or_none()
        return int(value) if value is not None else 0

    def pending_outbound(self) -> list[BufferedFact]:
        stmt = select(outbound_buffer).where(outbound_buffer.c.acked_at.is_(None)).order_by(outbound_buffer.c.seq)
        return [
            BufferedFact(
                seq=int(r.seq),
                kind=str(r.kind),
                chunk_id=str(r.chunk_id) if r.chunk_id is not None else None,
                payload=str(r.payload),
                created_at=r.created_at,
            )
            for r in self._all(stmt)
        ]

    # --- writes -------------------------------------------------------------

    def record_lease(self, lease: NewLease) -> None:
        with self._begin() as conn:
            conn.execute(
                leases.insert().values(
                    lease_id=lease.lease_id,
                    chunk_id=lease.chunk_id,
                    epoch=lease.epoch,
                    runner_id=lease.runner_id,
                    created_at=lease.created_at,
                )
            )
            conn.execute(
                lease_context.insert().values(
                    lease_id=lease.lease_id,
                    chunk_id=lease.chunk_id,
                    graph_id=lease.graph_id,
                    node_id=lease.node_id,
                    node_name=lease.node_name,
                    retries_max=lease.retries_max,
                    recorded_at=lease.created_at,
                )
            )
        _log.info(
            "lease minted", lease_id=lease.lease_id, chunk_id=lease.chunk_id, node=lease.node_name, epoch=lease.epoch
        )

    def record_spawn(self, lease_id: str, *, pid: int, process_start_time: str, session_id: str) -> None:
        with self._begin() as conn:
            conn.execute(
                leases.update()
                .where(leases.c.lease_id == lease_id)
                .values(pid=pid, process_start_time=process_start_time, session_id=session_id)
            )
        _log.info("worker spawned", lease_id=lease_id, pid=pid, session_id=session_id)

    def record_binding(self, *, chunk_id: str, environment_id: str, workdir: str, bound_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(
                env_bindings.insert().values(
                    chunk_id=chunk_id, environment_id=environment_id, workdir=workdir, bound_at=bound_at
                )
            )
        _log.info("env bound", chunk_id=chunk_id, environment_id=environment_id, workdir=workdir)

    def record_closure(self, *, lease_id: str, chunk_id: str, node_id: str, reason: str, closed_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(
                lease_closures.insert().values(
                    lease_id=lease_id, chunk_id=chunk_id, node_id=node_id, reason=reason, closed_at=closed_at
                )
            )
        _log.info("lease closed", lease_id=lease_id, chunk_id=chunk_id, reason=reason)

    def record_release(self, *, chunk_id: str, environment_id: str, released_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(
                binding_releases.insert().values(
                    chunk_id=chunk_id, environment_id=environment_id, released_at=released_at
                )
            )
        _log.info("env released", chunk_id=chunk_id, environment_id=environment_id)

    def enqueue_outbound(self, *, kind: str, chunk_id: str | None, payload: str, created_at: datetime) -> int:
        with self._begin() as conn:
            result = conn.execute(
                outbound_buffer.insert().values(kind=kind, chunk_id=chunk_id, payload=payload, created_at=created_at)
            )
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def ack_outbound(self, seq: int, *, acked_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(outbound_buffer.update().where(outbound_buffer.c.seq == seq).values(acked_at=acked_at))

    # --- plumbing -----------------------------------------------------------

    @staticmethod
    def _lease_select():  # type: ignore[no-untyped-def]
        return select(
            leases.c.lease_id,
            leases.c.chunk_id,
            leases.c.epoch,
            leases.c.runner_id,
            leases.c.pid,
            leases.c.process_start_time,
            leases.c.session_id,
            leases.c.created_at,
            lease_context.c.graph_id,
            lease_context.c.node_id,
            lease_context.c.node_name,
            lease_context.c.retries_max,
        ).join(lease_context, lease_context.c.lease_id == leases.c.lease_id)

    @staticmethod
    def _row_to_lease(r) -> LeaseRecord:  # type: ignore[no-untyped-def]
        return LeaseRecord(
            lease_id=str(r.lease_id),
            chunk_id=str(r.chunk_id),
            graph_id=str(r.graph_id),
            node_id=str(r.node_id),
            node_name=str(r.node_name),
            epoch=int(r.epoch),
            runner_id=str(r.runner_id),
            retries_max=int(r.retries_max),
            created_at=r.created_at,
            pid=int(r.pid) if r.pid is not None else None,
            process_start_time=str(r.process_start_time) if r.process_start_time is not None else None,
            session_id=str(r.session_id) if r.session_id is not None else None,
        )

    def _connect(self):  # type: ignore[no-untyped-def]
        try:
            return self._engine.connect()
        except SQLAlchemyError as exc:
            raise self._wrap(exc, "connect") from exc

    def _begin(self):  # type: ignore[no-untyped-def]
        try:
            return self._engine.begin()
        except SQLAlchemyError as exc:
            raise self._wrap(exc, "begin") from exc

    def _all(self, stmt):  # type: ignore[no-untyped-def]
        try:
            with self._engine.connect() as conn:
                return list(conn.execute(stmt))
        except SQLAlchemyError as exc:
            raise self._wrap(exc, "query") from exc

    @staticmethod
    def _wrap(exc: SQLAlchemyError, operation: str) -> RunnerStoreError:
        _log.error("runner store operation failed", operation=operation, detail=str(exc))
        return RunnerStoreError(f"runner store {operation} failed: {exc}")


def _conforms_runner_store(x: SqlAlchemyRunnerStore) -> IWriteRunnerStore:
    return x
