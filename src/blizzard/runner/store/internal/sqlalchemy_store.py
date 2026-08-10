"""SQLAlchemy adapter for the runner-store repository (package-private).

The one place the runner's facts touch the engine (``bzh:pluggable-seams``). All library usage
is confined here, and a driver failure is wrapped once into
:class:`~blizzard.runner.store.repository.RunnerStoreError` (``bzh:structlog-logging``). Every
derived query realizes the facts-only invariant in SQL (``bzh:facts-not-status``)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, and_, case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from blizzard.foundation.ids import SEGMENT_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.store.repository import (
    AskRecord,
    BufferedFact,
    BufferedTranscriptDelta,
    CheckResultRecord,
    ClosedLeaseRecord,
    EnvBindingRecord,
    EscalationRecord,
    GitCommitDeclarationRecord,
    IWriteRunnerStore,
    LeaseRecord,
    NewLease,
    OutboundFactRecord,
    ParkRecord,
    PoolHead,
    RunnerStoreError,
    TakeoverRecord,
    TranscriptSegmentLedgerRow,
    UsageTotals,
)
from blizzard.runner.store.schema import (
    asks,
    attachments,
    binding_releases,
    check_results,
    checks_ran,
    daemon_liveness,
    env_bindings,
    external_usage_samples,
    git_commit_declarations,
    heartbeats,
    hub_control,
    lease_closures,
    lease_context,
    lease_spawns,
    lease_tokens,
    leases,
    local_pause_facts,
    nudge_facts,
    outbound_buffer,
    park_facts,
    park_resumes,
    pause_park_resumes,
    pause_parks,
    requeues,
    resume_clears,
    resume_intents,
    route_tokens,
    session_ends,
    session_preamble_facts,
    takeover_ends,
    takeovers,
    transcript_outbound_buffer,
    transcript_segments,
    usage_facts,
    workspace_prompt,
)
from blizzard.wire.facts import USAGE_RECORDED

_log = get_logger("blizzard.runner.store")

# The caller-owned closure reason this store reads back to derive "open escalation"
# (issue #51).
_ESCALATED_REASON = "escalated"

# A fresh segment's placeholder, before its first pump read — the harness seam's own
# sentinel convention, restated rather than imported (the store never depends on it).
_NO_NORMALIZER_VERSION = ""


@dataclass(frozen=True)
class Unsuperseded:
    """A fact row stands while no superseding row exists — one correlated ``NOT EXISTS``.

    Correlated on the superseding row's own ordering column, an instant or an epoch, never
    a bare key ``NOT IN``: a re-mark above an earlier close reads as open again."""

    marker: Any
    conditions: tuple[Any, ...]

    @property
    def clause(self):  # type: ignore[no-untyped-def]
        return ~select(self.marker).where(*self.conditions).exists()


@dataclass(frozen=True)
class Unclosed:
    """A row stands while no closing row names its id — a plain ``NOT IN``.

    Its key is a fresh ULID per open, so there is no re-open-under-the-same-key hazard for
    the correlated form above to guard against."""

    key: Any
    closers: Any

    @property
    def clause(self):  # type: ignore[no-untyped-def]
        return self.key.not_in(select(self.closers))


# Pinned by tests/test_pin_runner_store.py::test_a_rebind_after_a_release_reads_as_held.
HELD_BINDING = Unsuperseded(
    binding_releases.c.id,
    (
        binding_releases.c.chunk_id == env_bindings.c.chunk_id,
        binding_releases.c.environment_id == env_bindings.c.environment_id,
        binding_releases.c.released_at >= env_bindings.c.bound_at,
    ),
)

# Pinned by tests/test_runner_restart_resume.py::test_remark_across_two_restarts_reopens_the_intent.
OPEN_INTENT = Unsuperseded(
    resume_clears.c.id,
    (
        resume_clears.c.lease_id == resume_intents.c.lease_id,
        resume_clears.c.cleared_at >= resume_intents.c.marked_at,
    ),
)

# A second pause under one lease is not masked by the first pause's resume.
OPEN_PAUSE_PARK = Unsuperseded(
    pause_park_resumes.c.id,
    (
        pause_park_resumes.c.lease_id == pause_parks.c.lease_id,
        pause_park_resumes.c.resumed_at >= pause_parks.c.parked_at,
    ),
)

# Correlated against ``open_escalations``'s own outer ``leases``/``lease_closures`` join.
_LATER_LEASE = leases.alias("later_escalation_leases")
LIVE_ESCALATION = Unsuperseded(
    _LATER_LEASE.c.lease_id,
    (_LATER_LEASE.c.chunk_id == leases.c.chunk_id, _LATER_LEASE.c.epoch > leases.c.epoch),
)

# ``>=``: a mint at the mark's own instant is the spawn the mark itself triggered
# (pinned by tests/test_pin_runner_store.py::test_a_same_instant_mint_consumes_its_requeue_mark).
UNCONSUMED_REQUEUE = Unsuperseded(
    leases.c.lease_id,
    (leases.c.chunk_id == requeues.c.chunk_id, leases.c.created_at >= requeues.c.requeued_at),
)

OPEN_TAKEOVER = Unclosed(takeovers.c.takeover_id, takeover_ends.c.takeover_id)
OPEN_LEASE = Unclosed(leases.c.lease_id, lease_closures.c.lease_id)


def _enqueue_transcript_final(conn, segment, *, at: datetime) -> None:  # type: ignore[no-untyped-def]
    """Enqueue a marker noting ``segment`` is finalized (issue #246) — a minimal row; the
    wire-shaped ``TranscriptSegmentRecord`` itself is rendered at the drain boundary from
    the ledger row (``bzh:dependency-inversion``). Ships unconditionally, regardless of
    ``[transcripts] ship`` or whether a pump ever ran."""
    conn.execute(
        transcript_outbound_buffer.insert().values(
            segment_id=str(segment.segment_id),
            chunk_id=str(segment.chunk_id),
            final=True,
            payload=json.dumps({"segment_id": str(segment.segment_id)}),
            created_at=at,
        )
    )


class SqlAlchemyRunnerStore:
    """Read-write runner store over a SQLAlchemy engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- reads --------------------------------------------------------------

    def list_active_leases(self) -> list[LeaseRecord]:
        stmt = self._lease_select().where(OPEN_LEASE.clause)
        return [self._row_to_lease(r) for r in self._all(stmt)]

    def active_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        stmt = (
            self._lease_select()
            .where(leases.c.chunk_id == chunk_id)
            .where(OPEN_LEASE.clause)
            .order_by(leases.c.created_at.desc())
        )
        rows = self._all(stmt)
        return self._row_to_lease(rows[0]) if rows else None

    def active_lease(self, lease_id: str) -> LeaseRecord | None:
        stmt = self._lease_select().where(leases.c.lease_id == lease_id).where(OPEN_LEASE.clause)
        rows = self._all(stmt)
        return self._row_to_lease(rows[0]) if rows else None

    def latest_lease_for_chunk(self, chunk_id: str) -> LeaseRecord | None:
        stmt = self._lease_select().where(leases.c.chunk_id == chunk_id).order_by(leases.c.created_at.desc())
        rows = self._all(stmt)
        return self._row_to_lease(rows[0]) if rows else None

    def latest_session_id(self, chunk_id: str, node_name: str | None) -> str | None:
        stmt = self._lease_select().where(leases.c.chunk_id == chunk_id).where(leases.c.session_id.is_not(None))
        if node_name is not None:
            stmt = stmt.where(lease_context.c.node_name == node_name)
        stmt = stmt.order_by(leases.c.created_at.desc(), leases.c.lease_id.desc())
        rows = self._all(stmt)
        return str(rows[0].session_id) if rows else None

    def pool_head(self, chunk_id: str, session_name: str) -> PoolHead | None:
        """The newest session-bearing lease stamping ``session_name`` — the pool's head.

        Same ordering and same session-bearing filter as :meth:`latest_session_id`,
        keyed on the stamped pool name rather than the node name."""
        stmt = (
            self._lease_select()
            .where(leases.c.chunk_id == chunk_id)
            .where(leases.c.session_id.is_not(None))
            .where(lease_context.c.session_name == session_name)
            .order_by(leases.c.created_at.desc(), leases.c.lease_id.desc())
        )
        rows = self._all(stmt)
        if not rows:
            return None
        row = rows[0]
        return PoolHead(
            session_id=str(row.session_id),
            lease_id=str(row.lease_id),
            resolved_model=row.resolved_model,
            resolved_effort=row.resolved_effort,
        )

    def session_context_tokens(self, session_id: str) -> int | None:
        """The newest usage fact for any lease running ``session_id``, summed to its
        context size. Joined through ``leases.session_id`` — ``usage_facts`` carries no
        session id of its own."""
        stmt = (
            select(
                usage_facts.c.cache_read_tokens,
                usage_facts.c.cache_create_tokens,
                usage_facts.c.input_tokens,
            )
            .join(leases, leases.c.lease_id == usage_facts.c.lease_id)
            .where(leases.c.session_id == session_id)
            .order_by(usage_facts.c.recorded_at.desc(), usage_facts.c.id.desc())
            .limit(1)
        )
        rows = self._all(stmt)
        if not rows:
            return None
        row = rows[0]
        return int(row.cache_read_tokens) + int(row.cache_create_tokens) + int(row.input_tokens)

    def session_invocation_count(self, session_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(usage_facts)
            .join(leases, leases.c.lease_id == usage_facts.c.lease_id)
            .where(leases.c.session_id == session_id)
        )
        rows = self._all(stmt)
        return int(rows[0][0]) if rows else 0

    def lease_for_session(self, session_id: str) -> LeaseRecord | None:
        """The newest lease that ran ``session_id`` — same ordering as `pool_head`."""
        stmt = (
            self._lease_select()
            .where(leases.c.session_id == session_id)
            .order_by(leases.c.created_at.desc(), leases.c.lease_id.desc())
        )
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

    def latest_spawn(self, lease_id: str) -> datetime | None:
        stmt = select(func.max(lease_spawns.c.spawned_at)).where(lease_spawns.c.lease_id == lease_id)
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
        stmt = select(env_bindings.c.environment_id).where(HELD_BINDING.clause).distinct()
        return [str(r.environment_id) for r in self._all(stmt)]

    def bindings_for_chunk(self, chunk_id: str) -> list[EnvBindingRecord]:
        stmt = (
            select(env_bindings)
            .where(env_bindings.c.chunk_id == chunk_id)
            .where(HELD_BINDING.clause)
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
        stmt = select(env_bindings.c.chunk_id).where(HELD_BINDING.clause).distinct()
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
        # A forced takeover's fence bump (issue #52) mints no local lease, so it is folded
        # in here alongside the lease-minted epochs.
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

    def transcript_segment(self, segment_id: str) -> TranscriptSegmentLedgerRow | None:
        rows = self._all(select(transcript_segments).where(transcript_segments.c.segment_id == segment_id))
        return self._row_to_transcript_segment(rows[0]) if rows else None

    def open_transcript_segments(self) -> list[TranscriptSegmentLedgerRow]:
        stmt = (
            select(transcript_segments)
            .where(transcript_segments.c.finalized_at.is_(None))
            .order_by(transcript_segments.c.segment_id)
        )
        return [self._row_to_transcript_segment(r) for r in self._all(stmt)]

    def chunk_transcript_shipped_bytes(self, chunk_id: str) -> int:
        stmt = select(func.coalesce(func.sum(transcript_segments.c.shipped_bytes), 0)).where(
            transcript_segments.c.chunk_id == chunk_id
        )
        with self._connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def outstanding_transcript_buffer_bytes(self) -> int:
        # Payloads are `json.dumps(ensure_ascii=True)`, so SQL `length()` (chars) agrees
        # with the encoded byte length here (same fact the pump's own `_byte_cost` relies on).
        stmt = select(func.coalesce(func.sum(func.length(transcript_outbound_buffer.c.payload)), 0)).where(
            transcript_outbound_buffer.c.acked_at.is_(None)
        )
        with self._connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def pending_transcript_outbound(self, *, limit: int | None = None) -> list[BufferedTranscriptDelta]:
        stmt = (
            select(transcript_outbound_buffer)
            .where(transcript_outbound_buffer.c.acked_at.is_(None))
            .order_by(transcript_outbound_buffer.c.seq)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return [
            BufferedTranscriptDelta(
                seq=int(r.seq),
                final=bool(r.final),
                segment_id=str(r.segment_id),
                chunk_id=str(r.chunk_id),
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
        return self.ask_parked_lease_ids() | self.pause_parked_lease_ids()

    def ask_parked_lease_ids(self) -> set[str]:
        stmt = select(park_facts.c.lease_id).where(park_facts.c.question_id.not_in(select(park_resumes.c.question_id)))
        return {str(r.lease_id) for r in self._all(stmt)}

    def pause_parked_lease_ids(self) -> set[str]:
        stmt = select(pause_parks.c.lease_id).where(OPEN_PAUSE_PARK.clause).distinct()
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
        # An ask whose lease has closed is never open — a backstop independent of which
        # path writes the retiring `park_resumes` row (blizzard#202).
        stmt = (
            select(asks)
            .where(asks.c.question_id.not_in(select(park_resumes.c.question_id)))
            .where(asks.c.lease_id.not_in(select(lease_closures.c.lease_id)))
            .order_by(asks.c.id.desc())
        )
        return [self._row_to_ask(r) for r in self._all(stmt)]

    def held_bindings(self) -> list[EnvBindingRecord]:
        stmt = select(env_bindings).where(HELD_BINDING.clause).order_by(env_bindings.c.bound_at)
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
        stmt = self._escalation_select().where(LIVE_ESCALATION.clause).order_by(lease_closures.c.closed_at.desc())
        return [self._row_to_escalation(r) for r in self._all(stmt)]

    def open_escalation_for_chunk(self, chunk_id: str) -> EscalationRecord | None:
        stmt = (
            self._escalation_select()
            .where(lease_closures.c.chunk_id == chunk_id)
            .where(LIVE_ESCALATION.clause)
            .order_by(lease_closures.c.closed_at.desc())
        )
        rows = self._all(stmt)
        return self._row_to_escalation(rows[0]) if rows else None

    def open_takeover_for_chunk(self, chunk_id: str) -> TakeoverRecord | None:
        stmt = (
            select(takeovers)
            .where(takeovers.c.chunk_id == chunk_id)
            .where(OPEN_TAKEOVER.clause)
            .order_by(takeovers.c.opened_at.desc())
        )
        rows = self._all(stmt)
        return self._row_to_takeover(rows[0]) if rows else None

    def open_takeover_chunk_ids(self) -> set[str]:
        stmt = select(takeovers.c.chunk_id).where(OPEN_TAKEOVER.clause).distinct()
        return {str(r.chunk_id) for r in self._all(stmt)}

    def open_takeovers(self) -> list[TakeoverRecord]:
        stmt = select(takeovers).where(OPEN_TAKEOVER.clause).order_by(takeovers.c.opened_at.desc())
        return [self._row_to_takeover(r) for r in self._all(stmt)]

    def pending_requeue_chunk_ids(self) -> set[str]:
        stmt = select(requeues.c.chunk_id).where(UNCONSUMED_REQUEUE.clause).distinct()
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

    def route_token(self, chunk_id: str) -> str | None:
        rows = self._all(select(route_tokens.c.token).where(route_tokens.c.chunk_id == chunk_id))
        return str(rows[0].token) if rows else None

    def lease_token_hash(self, lease_id: str) -> str | None:
        rows = self._all(select(lease_tokens.c.token_hash).where(lease_tokens.c.lease_id == lease_id))
        return str(rows[0].token_hash) if rows else None

    def attachments_for_lease(self, lease_id: str) -> dict[str, str]:
        newest = (
            select(attachments.c.name, func.max(attachments.c.id).label("id"))
            .where(attachments.c.lease_id == lease_id)
            .group_by(attachments.c.name)
            .subquery()
        )
        stmt = select(attachments.c.name, attachments.c.content).join(newest, attachments.c.id == newest.c.id)
        return {str(r.name): str(r.content) for r in self._all(stmt)}

    def git_commit_declarations_for_lease(self, lease_id: str) -> dict[tuple[str, str], GitCommitDeclarationRecord]:
        newest = (
            select(
                git_commit_declarations.c.environment_id,
                git_commit_declarations.c.repo,
                func.max(git_commit_declarations.c.id).label("id"),
            )
            .where(git_commit_declarations.c.lease_id == lease_id)
            .group_by(git_commit_declarations.c.environment_id, git_commit_declarations.c.repo)
            .subquery()
        )
        stmt = select(
            git_commit_declarations.c.environment_id,
            git_commit_declarations.c.repo,
            git_commit_declarations.c.branch,
            git_commit_declarations.c.commit,
        ).join(newest, git_commit_declarations.c.id == newest.c.id)
        return {
            (str(r.environment_id), str(r.repo)): GitCommitDeclarationRecord(
                environment_id=str(r.environment_id),
                repo=str(r.repo),
                branch=str(r.branch),
                commit=str(r.commit),
            )
            for r in self._all(stmt)
        }

    def nudge_fired(self, lease_id: str, epoch: int) -> bool:
        rows = self._all(
            select(nudge_facts.c.lease_id).where(and_(nudge_facts.c.lease_id == lease_id, nudge_facts.c.epoch == epoch))
        )
        return bool(rows)

    def checks_ran(self, lease_id: str, epoch: int) -> bool:
        rows = self._all(
            select(checks_ran.c.id).where(and_(checks_ran.c.lease_id == lease_id, checks_ran.c.epoch == epoch))
        )
        return bool(rows)

    def check_results_for_lease(self, lease_id: str, epoch: int) -> list[CheckResultRecord]:
        # Ordered by insert id so the results read back in the order the checks ran.
        rows = self._all(
            select(check_results)
            .where(and_(check_results.c.lease_id == lease_id, check_results.c.epoch == epoch))
            .order_by(check_results.c.id)
        )
        return [
            CheckResultRecord(command=str(r.command), passed=bool(r.passed), output_tail=str(r.output_tail))
            for r in rows
        ]

    def session_preamble_fingerprint(self, session_id: str) -> PreambleFingerprint | None:
        # Ordered on the autoincrement pk, not on `recorded_at` or implicit insert order
        # (`bzh:sql-portable`).
        rows = self._all(
            select(session_preamble_facts.c.blizzard_digest, session_preamble_facts.c.workspace_digest)
            .where(session_preamble_facts.c.session_id == session_id)
            .order_by(session_preamble_facts.c.id.desc())
            .limit(1)
        )
        if not rows:
            return None
        return PreambleFingerprint(blizzard=str(rows[0].blizzard_digest), workspace=str(rows[0].workspace_digest))

    def resume_intent_lease_ids(self) -> set[str]:
        stmt = select(resume_intents.c.lease_id).where(OPEN_INTENT.clause).distinct()
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
            # No spawn fact: fall back to the unscoped reading, which over-reports
            # "declared done" and so can only suppress a resume, never invent one.
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

    def last_external_usage_attempt_at(self) -> datetime | None:
        stmt = select(func.max(external_usage_samples.c.sampled_at))
        with self._connect() as conn:
            value = conn.execute(stmt).scalar_one_or_none()
        return value

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
                    session_name=lease.session_name,
                    resolved_model=lease.resolved_model,
                    resolved_effort=lease.resolved_effort,
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
            generation = int(
                conn.execute(
                    select(func.count()).select_from(lease_spawns).where(lease_spawns.c.lease_id == lease_id)
                ).scalar_one()
            )
            # Every start path reaching this transaction is a segment boundary (issue #246,
            # D1) — stamped here, not at the call sites, so a fourth can't miss it.
            context_row = conn.execute(
                select(leases.c.chunk_id, leases.c.epoch, lease_context.c.node_id)
                .select_from(leases.join(lease_context, leases.c.lease_id == lease_context.c.lease_id))
                .where(leases.c.lease_id == lease_id)
            ).one()
            # Carries a resumed session's cursor forward — the cross-lease case finds its
            # predecessor already finalized, so this reads regardless of finalization.
            prior_segment = conn.execute(
                select(transcript_segments)
                .where(transcript_segments.c.chunk_id == context_row.chunk_id)
                .where(transcript_segments.c.session_id == session_id)
                # `segment_id` tie-breaks `stamped_at` (`bzh:sql-portable`) — a same-instant
                # pair would otherwise pick nondeterministically across backends.
                .order_by(transcript_segments.c.stamped_at.desc(), transcript_segments.c.segment_id.desc())
                .limit(1)
            ).one_or_none()
            carried_cursor: str | None = None
            if prior_segment is not None:
                carried_cursor = str(prior_segment.cursor) if prior_segment.cursor is not None else None
                if prior_segment.finalized_at is None:
                    conn.execute(
                        transcript_segments.update()
                        .where(transcript_segments.c.segment_id == prior_segment.segment_id)
                        .values(finalized_at=spawned_at)
                    )
                    _enqueue_transcript_final(conn, prior_segment, at=spawned_at)
            conn.execute(
                transcript_segments.insert().values(
                    segment_id=Id.mint_at(SEGMENT_PREFIX, spawned_at).value,
                    chunk_id=str(context_row.chunk_id),
                    node_id=str(context_row.node_id),
                    epoch=int(context_row.epoch),
                    generation=generation,
                    lease_id=lease_id,
                    session_id=session_id,
                    cursor=carried_cursor,
                    shipped_bytes=0,
                    shipped_turns=0,
                    normalizer_version=_NO_NORMALIZER_VERSION,
                    harness_version=None,
                    truncated_reason=None,
                    shipping_stopped_reason=None,
                    finalized_at=None,
                    stamped_at=spawned_at,
                )
            )
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

    def record_closure(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        reason: str,
        closed_at: datetime,
        event_kind: str | None = None,
        event_payload: str | None = None,
    ) -> None:
        # The closure and its operational event land in ONE transaction, so a `kill -9`
        # can neither surface an event for a closure that never happened nor drop one (#125).
        with self._begin() as conn:
            conn.execute(
                lease_closures.insert().values(
                    lease_id=lease_id, chunk_id=chunk_id, node_id=node_id, reason=reason, closed_at=closed_at
                )
            )
            if event_kind is not None and event_payload is not None:
                conn.execute(
                    outbound_buffer.insert().values(
                        kind=event_kind,
                        chunk_id=chunk_id,
                        lease_id=lease_id,
                        payload=event_payload,
                        created_at=closed_at,
                    )
                )
            # Segments are final by step close (issue #246) — finalized atomically here, on
            # the transcript lane's OWN buffer (D3), never `outbound_buffer` above.
            open_segments = conn.execute(
                select(transcript_segments)
                .where(transcript_segments.c.lease_id == lease_id)
                .where(transcript_segments.c.finalized_at.is_(None))
            ).all()
            for segment in open_segments:
                conn.execute(
                    transcript_segments.update()
                    .where(transcript_segments.c.segment_id == segment.segment_id)
                    .values(finalized_at=closed_at)
                )
                _enqueue_transcript_final(conn, segment, at=closed_at)
        _log.info(
            "lease closed",
            lease_id=lease_id,
            chunk_id=chunk_id,
            reason=reason,
            transcript_segments_finalized=len(open_segments),
        )

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

    def mark_transcript_record_truncated(self, segment_id: str, *, reason: str, severity: int) -> bool:
        with self._begin() as conn:
            row = conn.execute(
                select(
                    transcript_segments.c.truncated_reason,
                    transcript_segments.c.truncated_reason_severity,
                    transcript_segments.c.truncated_reasons_warned,
                ).where(transcript_segments.c.segment_id == segment_id)
            ).first()
            if row is None:
                return False
            current_reason, current_severity, warned_json = row
            warned: list[str] = json.loads(warned_json) if warned_json is not None else []
            # Latched per (segment, reason) — a reason already warned never re-warns, no
            # matter how the display field below moves after it.
            already_warned = reason in warned
            values: dict[str, Any] = {}
            if not already_warned:
                values["truncated_reasons_warned"] = json.dumps([*warned, reason])
            # Worst-of, by the CALLER's own severity — the store holds no opinion on reasons.
            # `current_severity` is nullable and never backfilled: a row that took its reason
            # before that column existed reads NULL, which is not comparable.
            if current_reason != reason and (
                current_reason is None or current_severity is None or severity >= current_severity
            ):
                values["truncated_reason"] = reason
                values["truncated_reason_severity"] = severity
            if values:
                conn.execute(
                    transcript_segments.update().where(transcript_segments.c.segment_id == segment_id).values(**values)
                )
        changed = not already_warned
        if changed:
            _log.warning("transcript record truncated", segment_id=segment_id, reason=reason)
        return changed

    def stop_transcript_segment_shipping(self, segment_id: str, *, reason: str) -> bool:
        with self._begin() as conn:
            # `IS NULL` guard: a segment already stopped keeps its first reason.
            result = conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .where(transcript_segments.c.shipping_stopped_reason.is_(None))
                .values(shipping_stopped_reason=reason)
            )
        changed = result.rowcount > 0
        if changed:
            _log.warning("transcript segment stopped shipping", segment_id=segment_id, reason=reason)
        return changed

    def mark_sidechain_dropped_warned(self, segment_id: str, *, agent_id: str | None) -> bool:
        with self._begin() as conn:
            row = conn.execute(
                select(transcript_segments.c.sidechain_warned_agents).where(
                    transcript_segments.c.segment_id == segment_id
                )
            ).first()
            warned: list[str | None] = json.loads(row[0]) if row is not None and row[0] is not None else []
            if agent_id in warned:
                return False
            warned.append(agent_id)
            conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .values(sidechain_warned_agents=json.dumps(warned))
            )
        _log.warning("transcript segment dropped an unlinked sidechain", segment_id=segment_id, agent_id=agent_id)
        return True

    def ack_transcript_outbound(self, seq: int, *, acked_at: datetime) -> None:
        with self._begin() as conn:
            # Non-final rows are pruned outright, nothing reading an acked one; a final
            # marker stays, acked in place — its row is the exactly-once receipt.
            conn.execute(
                transcript_outbound_buffer.delete()
                .where(transcript_outbound_buffer.c.seq == seq)
                .where(transcript_outbound_buffer.c.final.is_(False))
            )
            conn.execute(
                transcript_outbound_buffer.update()
                .where(transcript_outbound_buffer.c.seq == seq)
                .where(transcript_outbound_buffer.c.final.is_(True))
                .values(acked_at=acked_at)
            )

    def record_transcript_deltas(
        self,
        *,
        segment_id: str,
        chunk_id: str,
        cursor: str | None,
        shipped_bytes: int,
        shipped_turns: int,
        normalizer_version: str,
        harness_version: str | None,
        payloads: list[str],
        created_at: datetime,
    ) -> list[int]:
        with self._begin() as conn:
            conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .values(
                    cursor=cursor,
                    shipped_bytes=shipped_bytes,
                    shipped_turns=shipped_turns,
                    normalizer_version=normalizer_version,
                    harness_version=harness_version,
                )
            )
            seqs: list[int] = []
            for payload in payloads:
                result = conn.execute(
                    transcript_outbound_buffer.insert().values(
                        segment_id=segment_id,
                        chunk_id=chunk_id,
                        final=False,
                        payload=payload,
                        created_at=created_at,
                    )
                )
                key = result.inserted_primary_key
                seqs.append(int(key[0]) if key is not None else 0)
        return seqs

    def advance_transcript_cursor(
        self, segment_id: str, *, cursor: str, normalizer_version: str, harness_version: str | None
    ) -> None:
        with self._begin() as conn:
            conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .values(cursor=cursor, normalizer_version=normalizer_version, harness_version=harness_version)
            )

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
        # Both inserts, one transaction: two would leave a `kill -9` window where the runner
        # has stopped claiming and the hub is never told (issue #43).
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

    def set_route_token(self, chunk_id: str, *, token: str, at: datetime) -> None:
        with self._begin() as conn:
            existing = conn.execute(
                select(route_tokens.c.chunk_id).where(route_tokens.c.chunk_id == chunk_id)
            ).one_or_none()
            if existing is None:
                conn.execute(route_tokens.insert().values(chunk_id=chunk_id, token=token, acquired_at=at))
            else:
                conn.execute(
                    route_tokens.update().where(route_tokens.c.chunk_id == chunk_id).values(token=token, acquired_at=at)
                )
        _log.info("route token stashed", chunk_id=chunk_id)

    def record_lease_token(self, lease_id: str, token_hash: str, at: datetime) -> None:
        # Delete-then-insert: a re-mint replaces the prior row under the `lease_id` PK, so
        # the old token is invalidated by construction.
        with self._begin() as conn:
            conn.execute(lease_tokens.delete().where(lease_tokens.c.lease_id == lease_id))
            conn.execute(lease_tokens.insert().values(lease_id=lease_id, token_hash=token_hash, minted_at=at))
        _log.info("lease token minted", lease_id=lease_id)

    def record_attachment(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        name: str,
        content: str,
        attached_at: datetime,
    ) -> None:
        # A single committed transaction — durable the instant this returns, so it
        # survives a `kill -9` right after (issue #113 Phase 2).
        with self._begin() as conn:
            conn.execute(
                attachments.insert().values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    name=name,
                    content=content,
                    attached_at=attached_at,
                )
            )
        _log.info("attachment recorded", lease_id=lease_id, name=name)

    def record_git_commit_declaration(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        environment_id: str,
        repo: str,
        branch: str,
        commit: str,
        declared_at: datetime,
    ) -> None:
        # A single committed transaction — durable the instant this returns, so it survives
        # a `kill -9` right after (issue #143).
        with self._begin() as conn:
            conn.execute(
                git_commit_declarations.insert().values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    environment_id=environment_id,
                    repo=repo,
                    branch=branch,
                    commit=commit,
                    declared_at=declared_at,
                )
            )
        _log.info("git-commit declaration recorded", lease_id=lease_id, repo=repo)

    def record_nudge_fired(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        # Check-then-insert in one transaction, mirroring `record_usage` — idempotent by
        # construction rather than a DB constraint (`bzh:sql-portable`).
        with self._begin() as conn:
            existing = conn.execute(
                select(nudge_facts.c.id).where(and_(nudge_facts.c.lease_id == lease_id, nudge_facts.c.epoch == epoch))
            ).one_or_none()
            if existing is not None:
                return
            conn.execute(nudge_facts.insert().values(lease_id=lease_id, epoch=epoch, nudged_at=at))
        _log.info("nudge fired", lease_id=lease_id, epoch=epoch)

    def record_check_results(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        results: list[CheckResultRecord],
        at: datetime,
    ) -> None:
        # Delete-then-insert in one transaction, so a re-run for the same `(lease, epoch)`
        # is latest-wins. Written BEFORE `runner:checks-recorded-when-marked`'s marker.
        with self._begin() as conn:
            conn.execute(
                check_results.delete().where(and_(check_results.c.lease_id == lease_id, check_results.c.epoch == epoch))
            )
            for result in results:
                conn.execute(
                    check_results.insert().values(
                        lease_id=lease_id,
                        chunk_id=chunk_id,
                        node_id=node_id,
                        epoch=epoch,
                        command=result.command,
                        passed=result.passed,
                        output_tail=result.output_tail,
                        ran_at=at,
                    )
                )
        _log.info("check results recorded", lease_id=lease_id, epoch=epoch, count=len(results))

    def record_checks_ran(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        # Check-then-insert in one transaction — idempotent by construction, not by a DB
        # constraint (`bzh:sql-portable`). Written AFTER `runner:checks-recorded-when-marked`.
        with self._begin() as conn:
            existing = conn.execute(
                select(checks_ran.c.id).where(and_(checks_ran.c.lease_id == lease_id, checks_ran.c.epoch == epoch))
            ).one_or_none()
            if existing is not None:
                return
            conn.execute(checks_ran.insert().values(lease_id=lease_id, epoch=epoch, ran_at=at))
        _log.info("checks marked ran", lease_id=lease_id, epoch=epoch)

    def record_session_preamble(self, session_id: str, *, fingerprint: PreambleFingerprint, at: datetime) -> None:
        # A plain append, no check-then-insert: a per-spawn fact whose newest row is the
        # answer, not a once-per-key guard.
        with self._begin() as conn:
            conn.execute(
                session_preamble_facts.insert().values(
                    session_id=session_id,
                    blizzard_digest=fingerprint.blizzard,
                    workspace_digest=fingerprint.workspace,
                    recorded_at=at,
                )
            )
        _log.info("session preamble recorded", session_id=session_id)

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
        # Both writes, one transaction: a usage fact the hub is never told about is never
        # reconciled later.
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
                # A replay of the exact same invocation — the row is already durable;
                # write nothing a second time.
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

    def record_external_usage_attempt(
        self, *, sampled_at: datetime, payload: str | None, report_kind: str, report_payload: str
    ) -> None:
        # The attempt row and its outbound report land in ONE transaction. Runner-scoped
        # (`chunk_id=None, lease_id=None`): a fact about the account, not a chunk or lease.
        with self._begin() as conn:
            conn.execute(external_usage_samples.insert().values(sampled_at=sampled_at, payload=payload))
            if payload is not None:
                conn.execute(
                    outbound_buffer.insert().values(
                        kind=report_kind, chunk_id=None, lease_id=None, payload=report_payload, created_at=sampled_at
                    )
                )
        _log.info("external subscription usage attempt recorded", sampled=payload is not None)

    # --- plumbing -----------------------------------------------------------

    @staticmethod
    def _row_to_transcript_segment(r) -> TranscriptSegmentLedgerRow:  # type: ignore[no-untyped-def]
        return TranscriptSegmentLedgerRow(
            segment_id=str(r.segment_id),
            chunk_id=str(r.chunk_id),
            node_id=str(r.node_id),
            epoch=int(r.epoch),
            generation=int(r.generation),
            lease_id=str(r.lease_id),
            session_id=str(r.session_id),
            cursor=str(r.cursor) if r.cursor is not None else None,
            shipped_bytes=int(r.shipped_bytes),
            shipped_turns=int(r.shipped_turns),
            normalizer_version=str(r.normalizer_version),
            harness_version=str(r.harness_version) if r.harness_version is not None else None,
            truncated_reason=str(r.truncated_reason) if r.truncated_reason is not None else None,
            shipping_stopped_reason=str(r.shipping_stopped_reason) if r.shipping_stopped_reason is not None else None,
            finalized_at=r.finalized_at,
            stamped_at=r.stamped_at,
        )

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
                # The escalated lease's session stamps (issue #144) — joined here rather
                # than read back per row.
                lease_context.c.session_name,
                lease_context.c.resolved_model,
                lease_context.c.resolved_effort,
            )
            .select_from(
                lease_closures.join(leases, leases.c.lease_id == lease_closures.c.lease_id).join(
                    lease_context, lease_context.c.lease_id == leases.c.lease_id
                )
            )
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
            session_name=r.session_name,
            resolved_model=r.resolved_model,
            resolved_effort=r.resolved_effort,
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
            # The session stamps (issue #144) — selected on the shared join rather than a
            # second query, so every lease read carries them.
            lease_context.c.session_name,
            lease_context.c.resolved_model,
            lease_context.c.resolved_effort,
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
            session_name=r.session_name,
            resolved_model=r.resolved_model,
            resolved_effort=r.resolved_effort,
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
