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

from sqlalchemy import Engine, and_, case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.store.repository import (
    AskRecord,
    BufferedFact,
    ClosedLeaseRecord,
    EnvBindingRecord,
    EscalationRecord,
    IWriteRunnerStore,
    LeaseRecord,
    NewLease,
    OutboundFactRecord,
    ParkRecord,
    RunnerStoreError,
    TakeoverRecord,
    UsageTotals,
)
from blizzard.runner.store.schema import (
    asks,
    binding_releases,
    daemon_liveness,
    env_bindings,
    heartbeats,
    hub_control,
    lease_closures,
    lease_context,
    lease_spawns,
    leases,
    local_pause_facts,
    outbound_buffer,
    park_facts,
    park_resumes,
    pause_park_resumes,
    pause_parks,
    requeues,
    resume_clears,
    resume_intents,
    session_ends,
    takeover_ends,
    takeovers,
    usage_facts,
    workspace_prompt,
)
from blizzard.wire.facts import USAGE_RECORDED

_log = get_logger("blizzard.runner.store")

# The closure reason `_escalate` (`runner/loop/steps.py`) records — the vocabulary is
# caller-owned (docstring precedent: `ClosedLeaseRecord`), this is the one place the
# store itself reads it back to derive "open escalation" (issue #51).
_ESCALATED_REASON = "escalated"


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


def _intent_is_open():  # type: ignore[no-untyped-def]
    """A resume-intent is **open** iff no clear for its lease is at or after the mark.

    Timestamp-aware, exactly like :func:`_binding_is_held`: a plain ``lease_id NOT IN
    clears`` would mask a **re-mark** — a still-in-flight lease marked again on a second
    graceful restart above an earlier clear — leaving the new intent invisible. Comparing
    against the clear instant un-masks it: a fresh mark stamped strictly later than its
    clear reads as open; the consumed one does not. ``>=`` keeps a same-instant clear
    winning (a clear stamped with its mark's own instant is a clear)."""
    return ~(
        select(resume_clears.c.id)
        .where(
            (resume_clears.c.lease_id == resume_intents.c.lease_id)
            & (resume_clears.c.cleared_at >= resume_intents.c.marked_at)
        )
        .exists()
    )


def _pause_park_is_open():  # type: ignore[no-untyped-def]
    """A pause-park is open iff no resume for its lease is at or after the park instant.

    Timestamp-aware exactly like _intent_is_open: a plain `lease_id NOT IN resumes` would
    mask a re-pause — paused, resumed, paused again under one lease — leaving the second
    pause invisible and its worker running. `>=` keeps a same-instant resume winning,
    consistent with _binding_is_held and _intent_is_open.
    """
    return ~(
        select(pause_park_resumes.c.id)
        .where(
            (pause_park_resumes.c.lease_id == pause_parks.c.lease_id)
            & (pause_park_resumes.c.resumed_at >= pause_parks.c.parked_at)
        )
        .exists()
    )


def _takeover_is_open():  # type: ignore[no-untyped-def]
    """A takeover is **open** iff no end fact names its ``takeover_id``.

    A plain ``NOT IN`` is safe here, unlike ``_pause_park_is_open``'s timestamp
    correlation: ``takeover_id`` is a fresh ULID per open (mirroring ``asks``'
    ``question_id``), so there is no re-open-under-the-same-key hazard to mask."""
    return takeovers.c.takeover_id.not_in(select(takeover_ends.c.takeover_id))


def _escalation_not_superseded():  # type: ignore[no-untyped-def]
    """An escalated ``lease_closures`` row not yet superseded by a later-epoch mint for
    the same chunk — correlated against the outer ``leases``/``lease_closures`` join
    (``open_escalations``'s and ``open_escalation_for_chunk``'s shared predicate)."""
    later = leases.alias("later_escalation_leases")
    return ~(
        select(later.c.lease_id)
        .where(later.c.chunk_id == leases.c.chunk_id)
        .where(later.c.epoch > leases.c.epoch)
        .exists()
    )


def _requeue_not_consumed():  # type: ignore[no-untyped-def]
    """A requeue mark not yet consumed by a later lease mint for the same chunk.

    ``>=`` keeps a same-instant mint winning, mirroring ``_pause_park_is_open``'s and
    ``_intent_is_open``'s timestamp correlation: the fresh spawn :func:`_reconcile_
    interrupted_claims` triggers off this mark stamps its lease no earlier than the mark
    itself, so a mint at or after the mark is always the one it caused."""
    return ~(
        select(leases.c.lease_id)
        .where(leases.c.chunk_id == requeues.c.chunk_id)
        .where(leases.c.created_at >= requeues.c.requeued_at)
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

    def latest_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        stmt = self._lease_select().where(leases.c.chunk_id == chunk_id).order_by(leases.c.created_at.desc())
        rows = self._all(stmt)
        return self._row_to_lease(rows[0]) if rows else None

    def lease(self, lease_id: str) -> LeaseRecord | None:
        stmt = self._lease_select().where(leases.c.lease_id == lease_id)
        rows = self._all(stmt)
        return self._row_to_lease(rows[0]) if rows else None

    def list_closed_leases(self, limit: int) -> list[ClosedLeaseRecord]:
        stmt = (
            self._lease_select()
            .add_columns(lease_closures.c.reason, lease_closures.c.closed_at)
            .join(lease_closures, lease_closures.c.lease_id == leases.c.lease_id)
            .order_by(lease_closures.c.closed_at.desc())
            .limit(limit)
        )
        return [
            ClosedLeaseRecord(lease=self._row_to_lease(r), reason=str(r.reason), closed_at=r.closed_at)
            for r in self._all(stmt)
        ]

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
        lease_stmt = select(func.max(leases.c.epoch)).where(leases.c.chunk_id == chunk_id)
        # A forced takeover's fence bump (issue #52) reports a new epoch to the hub
        # without minting a local lease, so a later real spawn must still see it —
        # folded in here alongside the lease-minted epochs, the fence source's one home.
        fence_stmt = select(func.max(takeovers.c.fence_epoch)).where(takeovers.c.chunk_id == chunk_id)
        with self._connect() as conn:
            lease_max = conn.execute(lease_stmt).scalar_one_or_none()
            fence_max = conn.execute(fence_stmt).scalar_one_or_none()
        return max(int(lease_max) if lease_max is not None else 0, int(fence_max) if fence_max is not None else 0)

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

    def recent_outbound(self, limit: int) -> list[OutboundFactRecord]:
        stmt = select(outbound_buffer).order_by(outbound_buffer.c.seq.desc()).limit(limit)
        return [
            OutboundFactRecord(
                seq=int(r.seq),
                kind=str(r.kind),
                chunk_id=str(r.chunk_id) if r.chunk_id is not None else None,
                lease_id=str(r.lease_id) if r.lease_id is not None else None,
                created_at=r.created_at,
                acked_at=r.acked_at,
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
        return self.ask_parked_lease_ids() | self.pause_parked_lease_ids()

    def ask_parked_lease_ids(self) -> set[str]:
        stmt = select(park_facts.c.lease_id).where(park_facts.c.question_id.not_in(select(park_resumes.c.question_id)))
        return {str(r.lease_id) for r in self._all(stmt)}

    def pause_parked_lease_ids(self) -> set[str]:
        stmt = select(pause_parks.c.lease_id).where(_pause_park_is_open()).distinct()
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

    def open_asks(self) -> list[AskRecord]:
        stmt = (
            select(asks).where(asks.c.question_id.not_in(select(park_resumes.c.question_id))).order_by(asks.c.id.desc())
        )
        return [self._row_to_ask(r) for r in self._all(stmt)]

    def held_bindings(self) -> list[EnvBindingRecord]:
        stmt = select(env_bindings).where(_binding_is_held()).order_by(env_bindings.c.bound_at)
        return [
            EnvBindingRecord(
                chunk_id=str(r.chunk_id),
                environment_id=str(r.environment_id),
                workdir=str(r.workdir),
                bound_at=r.bound_at,
            )
            for r in self._all(stmt)
        ]

    def open_escalations(self) -> list[EscalationRecord]:
        stmt = self._escalation_select().where(_escalation_not_superseded()).order_by(lease_closures.c.closed_at.desc())
        return [self._row_to_escalation(r) for r in self._all(stmt)]

    def open_escalation_for_chunk(self, chunk_id: str) -> EscalationRecord | None:
        stmt = (
            self._escalation_select()
            .where(lease_closures.c.chunk_id == chunk_id)
            .where(_escalation_not_superseded())
            .order_by(lease_closures.c.closed_at.desc())
        )
        rows = self._all(stmt)
        return self._row_to_escalation(rows[0]) if rows else None

    def open_takeover_for_chunk(self, chunk_id: str) -> TakeoverRecord | None:
        stmt = (
            select(takeovers)
            .where(takeovers.c.chunk_id == chunk_id)
            .where(_takeover_is_open())
            .order_by(takeovers.c.opened_at.desc())
        )
        rows = self._all(stmt)
        return self._row_to_takeover(rows[0]) if rows else None

    def open_takeover_chunk_ids(self) -> set[str]:
        stmt = select(takeovers.c.chunk_id).where(_takeover_is_open()).distinct()
        return {str(r.chunk_id) for r in self._all(stmt)}

    def open_takeovers(self) -> list[TakeoverRecord]:
        stmt = select(takeovers).where(_takeover_is_open()).order_by(takeovers.c.opened_at.desc())
        return [self._row_to_takeover(r) for r in self._all(stmt)]

    def pending_requeue_chunk_ids(self) -> set[str]:
        stmt = select(requeues.c.chunk_id).where(_requeue_not_consumed()).distinct()
        return {str(r.chunk_id) for r in self._all(stmt)}

    def hub_contact_at(self, runner_id: str) -> datetime | None:
        rows = self._all(select(hub_control.c.updated_at).where(hub_control.c.runner_id == runner_id))
        return rows[0].updated_at if rows else None

    def hub_paused(self, runner_id: str) -> bool:
        rows = self._all(select(hub_control.c.paused).where(hub_control.c.runner_id == runner_id))
        return bool(rows[0].paused) if rows else False

    def local_paused(self, runner_id: str) -> bool:
        rows = self._all(
            select(local_pause_facts.c.paused)
            .where(local_pause_facts.c.runner_id == runner_id)
            .order_by(local_pause_facts.c.id.desc())
            .limit(1)
        )
        return bool(rows[0].paused) if rows else False

    def workspace_prompt_override(self, workspace_id: str) -> str | None:
        rows = self._all(select(workspace_prompt.c.prompt).where(workspace_prompt.c.workspace_id == workspace_id))
        return str(rows[0].prompt) if rows else None

    def resume_intent_lease_ids(self) -> set[str]:
        stmt = select(resume_intents.c.lease_id).where(_intent_is_open()).distinct()
        return {str(r.lease_id) for r in self._all(stmt)}

    def session_ended_lease_ids(self) -> set[str]:
        newest_spawn = (
            select(lease_spawns.c.lease_id, func.max(lease_spawns.c.spawned_at).label("spawned_at"))
            .group_by(lease_spawns.c.lease_id)
            .subquery()
        )
        stmt = (
            select(session_ends.c.lease_id)
            .select_from(session_ends.outerjoin(newest_spawn, newest_spawn.c.lease_id == session_ends.c.lease_id))
            # No spawn fact = a lease minted before the crash-recovery-context revision: fall back to
            # the unscoped reading, which over-reports "declared done" and so can only suppress a
            # resume, never invent one.
            .where(or_(newest_spawn.c.spawned_at.is_(None), session_ends.c.ended_at >= newest_spawn.c.spawned_at))
            .distinct()
        )
        return {str(r.lease_id) for r in self._all(stmt)}

    def last_daemon_liveness(self) -> datetime | None:
        rows = self._all(select(func.max(daemon_liveness.c.alive_at).label("alive_at")))
        return rows[0].alive_at if rows and rows[0].alive_at is not None else None

    def lease_generation(self, lease_id: str) -> int:
        stmt = select(func.count()).select_from(lease_spawns).where(lease_spawns.c.lease_id == lease_id)
        with self._connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def lease_ids_for_chunk(self, chunk_id: str) -> list[str]:
        stmt = select(leases.c.lease_id).where(leases.c.chunk_id == chunk_id)
        return [str(r.lease_id) for r in self._all(stmt)]

    def usage_since(self, at: datetime) -> UsageTotals:
        stmt = select(
            func.coalesce(func.sum(usage_facts.c.input_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.output_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.cache_read_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.cache_create_tokens), 0),
            func.coalesce(func.sum(usage_facts.c.cost_usd), 0.0),
            func.coalesce(func.sum(case((usage_facts.c.cost_usd.is_(None), 1), else_=0)), 0),
        ).where(usage_facts.c.recorded_at >= at)
        with self._connect() as conn:
            row = conn.execute(stmt).one()
        return UsageTotals(
            input_tokens=int(row[0]),
            output_tokens=int(row[1]),
            cache_read_tokens=int(row[2]),
            cache_create_tokens=int(row[3]),
            cost_usd=float(row[4]),
            cost_partial=bool(row[5]),
        )

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

    def record_spawn(
        self, lease_id: str, *, pid: int, process_start_time: str, session_id: str, spawned_at: datetime
    ) -> None:
        with self._begin() as conn:
            conn.execute(
                leases.update()
                .where(leases.c.lease_id == lease_id)
                .values(pid=pid, process_start_time=process_start_time, session_id=session_id)
            )
            # One transaction with the in-place pid rewrite: the spawn generation and the process
            # it describes are one fact, and a crash between them would leave the two disagreeing.
            conn.execute(lease_spawns.insert().values(lease_id=lease_id, spawned_at=spawned_at))
        _log.info("worker spawned", lease_id=lease_id, pid=pid, session_id=session_id)

    def record_daemon_liveness(self, *, runner_id: str, alive_at: datetime) -> None:
        with self._begin() as conn:
            existing = conn.execute(
                select(daemon_liveness.c.runner_id).where(daemon_liveness.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(daemon_liveness.insert().values(runner_id=runner_id, alive_at=alive_at))
            else:
                conn.execute(
                    daemon_liveness.update().where(daemon_liveness.c.runner_id == runner_id).values(alive_at=alive_at)
                )
        _log.debug("daemon liveness stamped", runner_id=runner_id)

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

    def record_pause_park(self, *, lease_id: str, chunk_id: str, parked_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(pause_parks.insert().values(lease_id=lease_id, chunk_id=chunk_id, parked_at=parked_at))
        _log.info("chunk parked on operator pause", lease_id=lease_id, chunk_id=chunk_id)

    def record_pause_park_resume(self, *, lease_id: str, resumed_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(pause_park_resumes.insert().values(lease_id=lease_id, resumed_at=resumed_at))
        _log.info("pause park resumed", lease_id=lease_id)

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

    def record_local_pause(
        self, runner_id: str, *, paused: bool, at: datetime, by: str, report_kind: str, report_payload: str
    ) -> None:
        # Both inserts, one transaction: the brake and the report that makes it visible
        # land together or not at all. Two transactions would leave a `kill -9` window
        # where the runner has stopped claiming and the hub is never told — and nothing
        # reconciles that afterwards, since PULL only mirrors hub->runner. The buffer
        # delivers whenever the hub is next reachable, so this stays a local write.
        with self._begin() as conn:
            conn.execute(local_pause_facts.insert().values(runner_id=runner_id, paused=paused, set_at=at, set_by=by))
            conn.execute(
                outbound_buffer.insert().values(
                    kind=report_kind, chunk_id=None, lease_id=None, payload=report_payload, created_at=at
                )
            )
        _log.info("local pause fact recorded", runner_id=runner_id, paused=paused, set_by=by, report=report_kind)

    def set_workspace_prompt(self, workspace_id: str, *, prompt: str, at: datetime) -> None:
        with self._begin() as conn:
            existing = conn.execute(
                select(workspace_prompt.c.workspace_id).where(workspace_prompt.c.workspace_id == workspace_id)
            ).one_or_none()
            if existing is None:
                conn.execute(workspace_prompt.insert().values(workspace_id=workspace_id, prompt=prompt, updated_at=at))
            else:
                conn.execute(
                    workspace_prompt.update()
                    .where(workspace_prompt.c.workspace_id == workspace_id)
                    .values(prompt=prompt, updated_at=at)
                )
        _log.info("workspace prompt override set", workspace_id=workspace_id, length=len(prompt))

    def record_resume_intent(self, *, lease_id: str, marked_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(resume_intents.insert().values(lease_id=lease_id, marked_at=marked_at))
        _log.info("resume intent marked", lease_id=lease_id)

    def record_resume_clear(self, *, lease_id: str, cleared_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(resume_clears.insert().values(lease_id=lease_id, cleared_at=cleared_at))
        _log.info("resume intent cleared", lease_id=lease_id)

    def record_session_end(self, *, lease_id: str, ended_at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(session_ends.insert().values(lease_id=lease_id, ended_at=ended_at))
        _log.info("session end recorded", lease_id=lease_id)

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
        with self._begin() as conn:
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
        with self._begin() as conn:
            conn.execute(takeover_ends.insert().values(takeover_id=takeover_id, ended_at=ended_at))
        _log.info("takeover ended", takeover_id=takeover_id)

    def record_requeue(self, *, chunk_id: str, at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(requeues.insert().values(chunk_id=chunk_id, requeued_at=at))
        _log.info("chunk requeued locally", chunk_id=chunk_id)

    def record_usage(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        sample: UsageSample,
        recorded_at: datetime,
    ) -> None:
        # Both writes, one transaction — the same atomic local+outbound pairing
        # `record_local_pause` uses: a usage fact the hub is never told about is a chunk
        # whose board total silently under-reports, and nothing later reconciles it.
        with self._begin() as conn:
            existing = conn.execute(
                select(usage_facts.c.id).where(
                    and_(
                        usage_facts.c.lease_id == lease_id,
                        usage_facts.c.generation == generation,
                        usage_facts.c.kind == sample.kind,
                    )
                )
            ).one_or_none()
            if existing is not None:
                # A replay of the exact same invocation (a crash between this write and
                # the outbound enqueue, re-run by the next tick before the completion is
                # buffered) — the row is already durable; write nothing a second time.
                return
            conn.execute(
                usage_facts.insert().values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    generation=generation,
                    kind=sample.kind,
                    model=sample.model,
                    input_tokens=sample.input_tokens,
                    output_tokens=sample.output_tokens,
                    cache_read_tokens=sample.cache_read_tokens,
                    cache_create_tokens=sample.cache_create_tokens,
                    cost_usd=sample.cost_usd,
                    recorded_at=recorded_at,
                )
            )
            payload = json.dumps(
                {
                    "chunk_id": chunk_id,
                    "node_id": node_id,
                    "epoch": epoch,
                    "kind": sample.kind,
                    "model": sample.model,
                    "input_tokens": sample.input_tokens,
                    "output_tokens": sample.output_tokens,
                    "cache_read_tokens": sample.cache_read_tokens,
                    "cache_create_tokens": sample.cache_create_tokens,
                    "cost_usd": sample.cost_usd,
                }
            )
            conn.execute(
                outbound_buffer.insert().values(
                    kind=USAGE_RECORDED,
                    chunk_id=chunk_id,
                    lease_id=lease_id,
                    payload=payload,
                    created_at=recorded_at,
                )
            )
        _log.info(
            "usage fact recorded",
            lease_id=lease_id,
            chunk_id=chunk_id,
            generation=generation,
            kind=sample.kind,
            cost_usd=sample.cost_usd,
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
    def _escalation_select():  # type: ignore[no-untyped-def]
        return (
            select(
                lease_closures.c.lease_id,
                lease_closures.c.chunk_id,
                lease_closures.c.node_id,
                lease_closures.c.closed_at,
                leases.c.epoch,
                leases.c.session_id,
            )
            .select_from(lease_closures.join(leases, leases.c.lease_id == lease_closures.c.lease_id))
            .where(lease_closures.c.reason == _ESCALATED_REASON)
        )

    @staticmethod
    def _row_to_escalation(r) -> EscalationRecord:  # type: ignore[no-untyped-def]
        return EscalationRecord(
            lease_id=str(r.lease_id),
            chunk_id=str(r.chunk_id),
            node_id=str(r.node_id),
            epoch=int(r.epoch),
            session_id=str(r.session_id) if r.session_id is not None else None,
            closed_at=r.closed_at,
        )

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
