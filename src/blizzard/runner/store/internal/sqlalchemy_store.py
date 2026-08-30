"""SQLAlchemy adapter for the runner-store repository (package-private).

The one place the runner's facts touch the engine (``bzh:pluggable-seams``). Composes
:class:`~blizzard.runner.store.internal.lease_store.LeaseStore` (blizzard#410) so this
class still answers the whole ``IWriteRunnerStore`` surface while the remaining concepts
await their own extraction."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, and_, case, func, select
from sqlalchemy.exc import SQLAlchemyError

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import SEGMENT_PREFIX, Id
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.internal.base import (
    HELD_BINDING,
    LIVE_ESCALATION,
    NO_NORMALIZER_VERSION,
    OPEN_PAUSE_PARK,
    UNRESOLVED_ESCALATION,
    RunnerStoreConnections,
    Unclosed,
    Unsuperseded,
    enqueue_transcript_final,
)
from blizzard.runner.store.internal.lease_store import LeaseStore
from blizzard.runner.store.repository import (
    AskRecord,
    BufferedFact,
    BufferedTranscriptDelta,
    CheckResultRecord,
    ContextSampleState,
    EnvBindingRecord,
    EscalationRecord,
    GitCommitDeclarationRecord,
    GraphArtifactRecord,
    IWriteRunnerStore,
    OutboundFactRecord,
    ParkRecord,
    TakeoverRecord,
    TranscriptBackfillLease,
    TranscriptSegmentLedgerRow,
    UsageTotals,
)
from blizzard.runner.store.schema import (
    asks,
    attachments,
    binding_releases,
    check_results,
    checks_ran,
    context_samples,
    daemon_liveness,
    env_bindings,
    escalation_closures,
    external_usage_samples,
    git_commit_declarations,
    graph_artifacts,
    hub_control,
    lease_closures,
    lease_context,
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
    route_tokens,
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

# Every predicate below answers "what closes this?" (`bzh:open-facts-declare-closure`).
# The single-concept ones stay here; the shared ones moved to ``store/internal/base.py``.

# ``>=``: a mint at the mark's own instant is the spawn the mark itself triggered
# (pinned by tests/test_pin_runner_store.py::test_a_same_instant_mint_consumes_its_requeue_mark).
UNCONSUMED_REQUEUE = Unsuperseded(
    leases.c.lease_id,
    (leases.c.chunk_id == requeues.c.chunk_id, leases.c.created_at >= requeues.c.requeued_at),
)

OPEN_TAKEOVER = Unclosed(takeovers.c.takeover_id, takeover_ends.c.takeover_id)


class SqlAlchemyRunnerStore(LeaseStore):
    """Read-write runner store over a SQLAlchemy engine.

    Inherits the lease seam (blizzard#410); its own methods below answer whatever
    concept has not yet been extracted into its own ``store/internal/`` adapter."""

    def __init__(self, engine: Engine, errors: RunnerStoreErrorFactory) -> None:
        super().__init__(RunnerStoreConnections(engine, errors))
        self._engine = engine
        self._errors = errors

    # --- reads --------------------------------------------------------------

    def lease_for_open_takeover(self, lease_id: str) -> LeaseRecord | None:
        stmt = (
            self._lease_select()
            .join(takeovers, takeovers.c.lease_id == leases.c.lease_id)
            .where(leases.c.lease_id == lease_id)
            .where(OPEN_TAKEOVER.clause)
        )
        rows = self._all(stmt)
        return self._row_to_lease(rows[0]) if rows else None

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

    def has_unshipped_transcript_content(self, chunk_id: str) -> bool:
        stmt = (
            select(transcript_outbound_buffer.c.seq)
            .where(transcript_outbound_buffer.c.chunk_id == chunk_id)
            .where(transcript_outbound_buffer.c.acked_at.is_(None))
            .where(transcript_outbound_buffer.c.final.is_(False))
            .limit(1)
        )
        return bool(self._all(stmt))

    def transcript_backfill_leases(self) -> list[TranscriptBackfillLease]:
        # Correlated EXISTS on the session, not the lease: several leases share one
        # resumed session, and any segment for it means the session is already imported.
        has_segment = (
            select(transcript_segments.c.segment_id)
            .where(transcript_segments.c.session_id == leases.c.session_id)
            .exists()
        )
        stmt = (
            self._lease_select()
            .add_columns(has_segment.label("has_segment"))
            .where(leases.c.session_id.is_not(None))
            .order_by(leases.c.created_at, leases.c.lease_id)
        )
        return [
            TranscriptBackfillLease(
                lease_id=str(r.lease_id),
                chunk_id=str(r.chunk_id),
                node_id=str(r.node_id),
                epoch=int(r.epoch),
                session_id=str(r.session_id),
                has_segment=bool(r.has_segment),
            )
            for r in self._all(stmt)
        ]

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
        stmt = (
            self._escalation_select()
            .where(LIVE_ESCALATION.clause)
            .where(UNRESOLVED_ESCALATION.clause)
            .order_by(lease_closures.c.closed_at.desc())
        )
        return [self._row_to_escalation(r) for r in self._all(stmt)]

    def open_escalation_for_chunk(self, chunk_id: str) -> EscalationRecord | None:
        stmt = (
            self._escalation_select()
            .where(lease_closures.c.chunk_id == chunk_id)
            .where(LIVE_ESCALATION.clause)
            .where(UNRESOLVED_ESCALATION.clause)
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

    def graph_artifacts_for_graph(self, graph_id: str) -> list[GraphArtifactRecord]:
        # Explicit order_by (`bzh:sql-portable`) — authored `artifacts:` position, not insert order.
        rows = self._all(
            select(graph_artifacts).where(graph_artifacts.c.graph_id == graph_id).order_by(graph_artifacts.c.ordinal)
        )
        return [
            GraphArtifactRecord(
                name=str(r.name), ordinal=int(r.ordinal), kind=ArtifactKind(str(r.kind)), content=str(r.content)
            )
            for r in rows
        ]

    def last_daemon_liveness(self) -> datetime | None:
        rows = self._all(select(func.max(daemon_liveness.c.alive_at).label("alive_at")))
        return rows[0].alive_at if rows and rows[0].alive_at is not None else None

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

    def record_graph_artifacts(
        self, *, graph_id: str, artifacts: list[GraphArtifactRecord], recorded_at: datetime
    ) -> None:
        # A mint declaring nothing writes no row, so the presence check below would never
        # find one and every later lease off that mint would redo the check and re-log it.
        if not artifacts:
            return
        # Check-then-insert in one transaction (`bzh:sql-portable`) — an immutable mint's
        # declarations never change, so a second call for the same graph_id is a no-op.
        with self._begin() as conn:
            existing = conn.execute(
                select(graph_artifacts.c.graph_id).where(graph_artifacts.c.graph_id == graph_id)
            ).first()
            if existing is not None:
                return
            for artifact in artifacts:
                conn.execute(
                    graph_artifacts.insert().values(
                        graph_id=graph_id,
                        name=artifact.name,
                        ordinal=artifact.ordinal,
                        kind=artifact.kind.value,
                        content=artifact.content,
                        recorded_at=recorded_at,
                    )
                )
        _log.info("graph artifacts pinned", graph_id=graph_id, count=len(artifacts))

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
        agent_tool_use_ids: dict[str, str] | None = None,
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
                    **self._merged_agent_tool_use_ids(conn, segment_id, agent_tool_use_ids),
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

    def open_transcript_segment(
        self,
        *,
        chunk_id: str,
        node_id: str,
        epoch: int,
        generation: int,
        lease_id: str,
        session_id: str,
        stamped_at: datetime,
        supersedes: str | None = None,
    ) -> str:
        segment_id = Id.mint_at(SEGMENT_PREFIX, stamped_at).value
        with self._begin() as conn:
            conn.execute(
                transcript_segments.insert().values(
                    segment_id=segment_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    generation=generation,
                    lease_id=lease_id,
                    session_id=session_id,
                    cursor=None,
                    shipped_bytes=0,
                    shipped_turns=0,
                    normalizer_version=NO_NORMALIZER_VERSION,
                    harness_version=None,
                    truncated_reason=None,
                    shipping_stopped_reason=None,
                    supersedes=supersedes,
                    finalized_at=None,
                    stamped_at=stamped_at,
                )
            )
        _log.info("transcript segment opened", segment_id=segment_id, lease_id=lease_id, session_id=session_id)
        return segment_id

    def finalize_transcript_segment(self, segment_id: str, *, finalized_at: datetime) -> bool:
        with self._begin() as conn:
            # The open-only guard and the marker share this transaction, so a second call
            # can neither re-finalize nor enqueue a second final row.
            segment = conn.execute(
                select(transcript_segments)
                .where(transcript_segments.c.segment_id == segment_id)
                .where(transcript_segments.c.finalized_at.is_(None))
            ).one_or_none()
            if segment is None:
                return False
            conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .values(finalized_at=finalized_at)
            )
            enqueue_transcript_final(conn, segment, at=finalized_at)
        _log.info("transcript segment finalized", segment_id=segment_id)
        return True

    def advance_transcript_cursor(
        self,
        segment_id: str,
        *,
        cursor: str,
        normalizer_version: str,
        harness_version: str | None,
        agent_tool_use_ids: dict[str, str] | None = None,
    ) -> None:
        with self._begin() as conn:
            conn.execute(
                transcript_segments.update()
                .where(transcript_segments.c.segment_id == segment_id)
                .values(
                    cursor=cursor,
                    normalizer_version=normalizer_version,
                    harness_version=harness_version,
                    **self._merged_agent_tool_use_ids(conn, segment_id, agent_tool_use_ids),
                )
            )

    @staticmethod
    def _merged_agent_tool_use_ids(conn: Connection, segment_id: str, learned: dict[str, str] | None) -> dict[str, str]:
        """The stored map merged with this window's pairs, as `values()` kwargs — empty when
        nothing was learned, so the column is left untouched rather than rewritten. Merged in
        the CALLER's transaction: a pair persisted without the cursor that read it re-learns
        nothing (blizzard#338)."""
        if not learned:
            return {}
        row = conn.execute(
            select(transcript_segments.c.agent_tool_use_ids).where(transcript_segments.c.segment_id == segment_id)
        ).first()
        stored: dict[str, str] = json.loads(row[0]) if row is not None and row[0] else {}
        # Stored wins: the first window to name a pair saw the `tool_result` that defines it,
        # and a later re-read of the same result must not renumber an established link.
        return {"agent_tool_use_ids": json.dumps({**learned, **stored})}

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
    ) -> int:
        # Both inserts, one transaction: two would leave a `kill -9` window where the runner
        # has stopped claiming and the hub is never told (issue #43).
        with self._begin() as conn:
            conn.execute(local_pause_facts.insert().values(runner_id=runner_id, paused=paused, set_at=at, set_by=by))
            result = conn.execute(
                outbound_buffer.insert().values(
                    kind=report_kind, chunk_id=None, lease_id=None, payload=report_payload, created_at=at
                )
            )
        _log.info("local pause fact recorded", runner_id=runner_id, paused=paused, set_by=by, report=report_kind)
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

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

    def clear_workspace_prompt(self, workspace_id: str) -> bool:
        with self._begin() as conn:
            deleted = conn.execute(
                workspace_prompt.delete().where(workspace_prompt.c.workspace_id == workspace_id)
            ).rowcount
        _log.info("workspace prompt override cleared", workspace_id=workspace_id, existed=bool(deleted))
        return bool(deleted)

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

    def record_escalation_closure(self, *, chunk_id: str, reason: str, at: datetime) -> None:
        with self._begin() as conn:
            conn.execute(escalation_closures.insert().values(chunk_id=chunk_id, reason=reason, closed_at=at))
        _log.info("escalation closed by the hub", chunk_id=chunk_id, reason=reason)

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
    ) -> int | None:
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
                return None
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
            result = conn.execute(
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
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def context_sample_state(self, lease_id: str) -> ContextSampleState | None:
        stmt = select(
            func.max(context_samples.c.sampled_at).label("last_sampled_at"),
            func.max(context_samples.c.context_tokens).label("max_context_tokens"),
        ).where(context_samples.c.lease_id == lease_id)
        rows = self._all(stmt)
        # An aggregate over no rows is one row of NULLs, not zero rows — the NULL is the
        # "never sampled" signal here, never a `0` that would read as a real measurement.
        if not rows or rows[0].last_sampled_at is None:
            return None
        row = rows[0]
        return ContextSampleState(
            last_sampled_at=as_utc(row.last_sampled_at),  # the anchor is subtracted from `now`
            # NULL here means every attempt so far measured nothing — `MAX` skips NULLs, so this
            # is only NULL when no row carries a measurement at all.
            max_context_tokens=int(row.max_context_tokens) if row.max_context_tokens is not None else None,
        )

    def record_context_sample(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        session_id: str,
        context_tokens: int | None,
        sampled_at: datetime,
        report_kind: str = "",
        report_payload: str = "",
    ) -> int | None:
        # The sample row and any outbound report land in ONE transaction, as the external
        # usage sampler below does — a warning buffered without its sample would re-fire.
        seq: int | None = None
        with self._begin() as conn:
            conn.execute(
                context_samples.insert().values(
                    lease_id=lease_id,
                    session_id=session_id,
                    context_tokens=context_tokens,
                    sampled_at=sampled_at,
                )
            )
            if report_kind:
                result = conn.execute(
                    outbound_buffer.insert().values(
                        kind=report_kind,
                        chunk_id=chunk_id,
                        lease_id=lease_id,
                        payload=report_payload,
                        created_at=sampled_at,
                    )
                )
                key = result.inserted_primary_key
                seq = int(key[0]) if key is not None else 0
        if report_kind:
            _log.warning(
                "session context crossed the warn line",
                lease_id=lease_id,
                session_id=session_id,
                context_tokens=context_tokens,
            )
        return seq

    def record_external_usage_attempt(
        self, *, sampled_at: datetime, payload: str | None, report_kind: str, report_payload: str
    ) -> int | None:
        # The attempt row and its outbound report land in ONE transaction. Runner-scoped
        # (`chunk_id=None, lease_id=None`): a fact about the account, not a chunk or lease.
        seq: int | None = None
        with self._begin() as conn:
            conn.execute(external_usage_samples.insert().values(sampled_at=sampled_at, payload=payload))
            if payload is not None:
                result = conn.execute(
                    outbound_buffer.insert().values(
                        kind=report_kind, chunk_id=None, lease_id=None, payload=report_payload, created_at=sampled_at
                    )
                )
                key = result.inserted_primary_key
                seq = int(key[0]) if key is not None else 0
        _log.info("external subscription usage attempt recorded", sampled=payload is not None)
        return seq

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
            supersedes=str(r.supersedes) if r.supersedes is not None else None,
            finalized_at=r.finalized_at,
            stamped_at=r.stamped_at,
            agent_tool_use_ids=json.loads(r.agent_tool_use_ids) if r.agent_tool_use_ids else {},
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

    # ``_lease_select``/``_row_to_lease`` are inherited from
    # :class:`~blizzard.runner.store.internal.lease_store.LeaseStore` (blizzard#410).

    def _connect(self):  # type: ignore[no-untyped-def]
        try:
            return self._engine.connect()
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="connect") from exc

    def _begin(self):  # type: ignore[no-untyped-def]
        try:
            return self._engine.begin()
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="begin") from exc

    def _all(self, stmt):  # type: ignore[no-untyped-def]
        try:
            with self._engine.connect() as conn:
                return list(conn.execute(stmt))
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="query") from exc


def _conforms_runner_store(x: SqlAlchemyRunnerStore) -> IWriteRunnerStore:
    return x
