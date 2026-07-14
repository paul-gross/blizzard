"""SQLAlchemy adapter for the runner-store repository (package-private).

The one place the reconciliation loop's facts touch the engine (``bzh:pluggable-seams``).
All library usage is confined here; a driver failure is wrapped once into
:class:`~blizzard.runner.store.repository.RunnerStoreError` (logged at the wrap
site, ``bzh:structlog-logging``) so loop code never depends on SQLAlchemy's
exceptions. Every derived query realizes the facts-only invariant in SQL:
active = no closure, held = no release (``bzh:facts-not-status``).
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Engine, and_, func, select
from sqlalchemy.exc import SQLAlchemyError

from blizzard.foundation.logging import get_logger
from blizzard.runner.store.repository import (
    AskRecord,
    BufferedFact,
    EnvBindingRecord,
    IWriteRunnerStore,
    LeaseRecord,
    NewLease,
    ParkRecord,
    RunnerStoreError,
)
from blizzard.runner.store.schema import (
    asks,
    binding_releases,
    env_bindings,
    heartbeats,
    hub_control,
    lease_closures,
    lease_context,
    leases,
    outbound_buffer,
    park_facts,
    park_resumes,
)

_log = get_logger("blizzard.runner.store")


def _binding_is_held():  # type: ignore[no-untyped-def]
    """A binding is **held** iff no release for its ``(chunk, env)`` is at or after it.

    Timestamp-aware: a plain ``env_id NOT IN releases`` set-difference would mask a
    **re-bind** — the same ``(chunk, env)`` bound again after a release (interrupted-claim
    recovery) — leaving a valid new binding invisible forever. Comparing against the
    release instant un-masks it: a fresh binding re-taken *after* an earlier release has no
    release at-or-after it, so it reads as held; the original binding does, so it does not
    (``bzh:facts-not-status``). ``>=`` keeps a same-instant release winning (a release
    stamped with its binding's own instant is a release), while a genuine re-bind is always
    stamped strictly later, so it is never spuriously masked."""
    return ~(
        select(binding_releases.c.id)
        .where(
            (binding_releases.c.chunk_id == env_bindings.c.chunk_id)
            & (binding_releases.c.environment_id == env_bindings.c.environment_id)
            & (binding_releases.c.released_at >= env_bindings.c.bound_at)
        )
        .exists()
    )


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

    def active_lease(self, lease_id: str) -> LeaseRecord | None:
        stmt = (
            self._lease_select()
            .where(leases.c.lease_id == lease_id)
            .where(leases.c.lease_id.not_in(select(lease_closures.c.lease_id)))
        )
        rows = self._all(stmt)
        return self._row_to_lease(rows[0]) if rows else None

    def latest_heartbeat(self, lease_id: str) -> datetime | None:
        stmt = select(func.max(heartbeats.c.beat_at)).where(heartbeats.c.lease_id == lease_id)
        with self._connect() as conn:
            value = conn.execute(stmt).scalar_one_or_none()
        return value

    def pending_submission_lease_ids(self) -> set[str]:
        stmt = select(outbound_buffer.c.lease_id).where(
            and_(
                outbound_buffer.c.acked_at.is_(None),
                outbound_buffer.c.kind.in_(("completion.submitted", "decision.submitted")),
                outbound_buffer.c.lease_id.is_not(None),
            )
        )
        return {str(r.lease_id) for r in self._all(stmt)}

    def held_environment_ids(self) -> list[str]:
        stmt = select(env_bindings.c.environment_id).where(_binding_is_held()).distinct()
        return [str(r.environment_id) for r in self._all(stmt)]

    def bindings_for_chunk(self, chunk_id: str) -> list[EnvBindingRecord]:
        stmt = (
            select(env_bindings)
            .where(env_bindings.c.chunk_id == chunk_id)
            .where(_binding_is_held())
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
        stmt = select(env_bindings.c.chunk_id).where(_binding_is_held()).distinct()
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
                lease_id=str(r.lease_id) if r.lease_id is not None else None,
                payload=str(r.payload),
                created_at=r.created_at,
            )
            for r in self._all(stmt)
        ]

    def unforwarded_ask(self, lease_id: str) -> AskRecord | None:
        stmt = (
            select(asks)
            .where(asks.c.lease_id == lease_id)
            .where(asks.c.question_id.not_in(select(park_facts.c.question_id)))
            .order_by(asks.c.id.desc())
        )
        rows = self._all(stmt)
        return self._row_to_ask(rows[0]) if rows else None

    def parked_lease_ids(self) -> set[str]:
        stmt = select(park_facts.c.lease_id).where(park_facts.c.question_id.not_in(select(park_resumes.c.question_id)))
        return {str(r.lease_id) for r in self._all(stmt)}

    def open_park(self, lease_id: str) -> ParkRecord | None:
        stmt = (
            select(park_facts)
            .where(park_facts.c.lease_id == lease_id)
            .where(park_facts.c.question_id.not_in(select(park_resumes.c.question_id)))
            .order_by(park_facts.c.id.desc())
        )
        rows = self._all(stmt)
        if not rows:
            return None
        r = rows[0]
        return ParkRecord(
            lease_id=str(r.lease_id),
            chunk_id=str(r.chunk_id),
            question_id=str(r.question_id),
            parked_at=r.parked_at,
        )

    def hub_paused(self, runner_id: str) -> bool:
        rows = self._all(select(hub_control.c.paused).where(hub_control.c.runner_id == runner_id))
        return bool(rows[0].paused) if rows else False

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

    def record_heartbeat(self, *, lease_id: str, beat_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(heartbeats.insert().values(lease_id=lease_id, beat_at=beat_at))
        _log.debug("heartbeat recorded", lease_id=lease_id)

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

    def enqueue_outbound(
        self, *, kind: str, chunk_id: str | None, lease_id: str | None, payload: str, created_at: datetime
    ) -> int:
        with self._begin() as conn:
            result = conn.execute(
                outbound_buffer.insert().values(
                    kind=kind, chunk_id=chunk_id, lease_id=lease_id, payload=payload, created_at=created_at
                )
            )
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def ack_outbound(self, seq: int, *, acked_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(outbound_buffer.update().where(outbound_buffer.c.seq == seq).values(acked_at=acked_at))

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
        with self._begin() as conn:
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
        with self._begin() as conn:
            conn.execute(
                park_facts.insert().values(
                    lease_id=lease_id, chunk_id=chunk_id, question_id=question_id, parked_at=parked_at
                )
            )
        _log.info("chunk parked on question", lease_id=lease_id, chunk_id=chunk_id, question_id=question_id)

    def record_park_resume(self, *, lease_id: str, question_id: str, resumed_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(
                park_resumes.insert().values(lease_id=lease_id, question_id=question_id, resumed_at=resumed_at)
            )
        _log.info("park resumed with answer", lease_id=lease_id, question_id=question_id)

    def set_hub_paused(self, runner_id: str, *, paused: bool, at: datetime) -> None:
        with self._begin() as conn:
            existing = conn.execute(
                select(hub_control.c.runner_id).where(hub_control.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(hub_control.insert().values(runner_id=runner_id, paused=paused, updated_at=at))
            else:
                conn.execute(
                    hub_control.update()
                    .where(hub_control.c.runner_id == runner_id)
                    .values(paused=paused, updated_at=at)
                )

    # --- plumbing -----------------------------------------------------------

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
