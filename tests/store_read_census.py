"""Read-method census for the store-read-index gate (blizzard#525, Phase 2).

Maps every reflected ``IRead*`` Protocol method to a recipe that exercises it against a
production-wired, migrated-to-head store — the seeded world :func:`build_runner_world`
builds once per gate run, entirely through each concept's own write Protocol
(``bzh:matrix-tier-rules``), never raw SQL. ``RUNNER_EXEMPTIONS`` carries the runner read
Protocols backed by no SQL at all, each reasoned.

Split by store at the module level (``RUNNER_CENSUS``/``RUNNER_EXEMPTIONS``) rather than
one generic structure, so Phase 3 can add a parallel ``HUB_CENSUS``/``HUB_EXEMPTIONS``
section here without reshaping what the runner half already owns.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.logging import get_logger
from blizzard.runner.auth.tokens import IReadTokenRepository
from blizzard.runner.composition import build_stores
from blizzard.runner.domain.artifacts import GraphArtifactRecord, IReadGraphArtifactRepository
from blizzard.runner.domain.asks import IReadAskRepository
from blizzard.runner.domain.attachments import IReadAttachmentRepository
from blizzard.runner.domain.checks import CheckResultRecord, IReadCheckRepository
from blizzard.runner.domain.elicitation import IReadElicitationRepository
from blizzard.runner.domain.escalations import IReadEscalationRepository
from blizzard.runner.domain.git_commit_declaration import IReadGitCommitDeclarationRepository
from blizzard.runner.domain.leases import (
    IReadLeaseLivenessRepository,
    IReadLeaseRecordRepository,
    IReadLeaseResumeIntentRepository,
    IReadLeaseSessionRepository,
    NewLease,
)
from blizzard.runner.domain.outbound import IReadOutboundRepository
from blizzard.runner.domain.pause import IReadPauseRepository
from blizzard.runner.domain.requeue import IReadRequeueRepository
from blizzard.runner.domain.takeover import IReadTakeoverRepository
from blizzard.runner.domain.usage import IReadUsageRepository
from blizzard.runner.environments.repository import IReadEnvironmentRepository
from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.harness.workspace_prompts import IReadWorkspacePromptRepository
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.stores import RunnerReadStores, RunnerStores
from blizzard.runner.transcripts.archived_repository import IReadArchivedTranscriptRepository
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository
from blizzard.runner.transcripts.repository import IReadTranscriptRepository

_BASE = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)


def _t(offset_seconds: float) -> datetime:
    """A deterministic, strictly-ordered stamp — the seed's own injected-clock stand-in
    (``bzh:injected-clock``): every write below stamps explicitly, never a driver default."""
    return _BASE + timedelta(seconds=offset_seconds)


RUNNER_ID = "r1"
GRAPH_ID = "gr_1"


@dataclass(frozen=True)
class RunnerWorld:
    """The runner store's module-scoped seeded world — one instance built once
    (:func:`build_runner_world`) and driven by every recipe in :data:`RUNNER_CENSUS`.

    ``stores``/``read`` are the same production-wired adapters, the latter narrowed to
    ``RunnerReadStores`` over the former's own instances (D1) — recipes read through
    ``read``, mirroring the one collaborator every controller-facing caller resolves
    through in production."""

    engine: Engine
    stores: RunnerStores
    read: RunnerReadStores
    chunk_1: str
    chunk_2: str
    chunk_3: str
    chunk_4: str
    chunk_5: str
    chunk_6: str
    chunk_7: str
    node_a: str
    lease_1: str
    lease_2: str
    lease_3: str
    lease_5: str
    lease_7: str
    lease_8: str
    session_1: str
    transcript_segment_id: str
    workspace_id: str
    usage_slug: str


def build_runner_world(engine: Engine) -> RunnerWorld:
    """Seed a migrated-to-head runner store through its own write Protocols, so every
    read method in :data:`RUNNER_CENSUS` reaches real, non-empty behavior — the store
    bundle comes from :func:`~blizzard.runner.composition.build_stores`, the production
    wiring (D2), never a hand-rolled adapter or ``tests/runner_fakes.py``'s flat fake."""
    stores = build_stores(engine, errors=RunnerStoreErrorFactory(get_logger("test")))

    chunk_1, chunk_2, chunk_3 = "ch_1", "ch_2", "ch_3"
    chunk_4, chunk_5, chunk_6, chunk_7 = "ch_4", "ch_5", "ch_6", "ch_7"
    node_a, node_b, node_c, node_d = "nd_a", "nd_b", "nd_c", "nd_d"
    node_e, node_f, node_g = "nd_e", "nd_f", "nd_g"

    # --- chunk 1: a closed-then-active lease pair sharing one resumed session ---------
    lease_1, lease_2 = "lease_1a", "lease_1b"
    stores.lease_record.record_lease(
        NewLease(
            lease_id=lease_1,
            chunk_id=chunk_1,
            graph_id=GRAPH_ID,
            node_id=node_a,
            node_name="build",
            epoch=1,
            runner_id=RUNNER_ID,
            retries_max=3,
            created_at=_t(0),
            session_name="pool-a",
            resolved_model="model-a",
            resolved_effort="medium",
        )
    )
    stores.liveness.record_spawn(lease_1, pid=100, process_start_time="st-100", session_id="sess-1", spawned_at=_t(1))
    stores.liveness.record_heartbeat(lease_id=lease_1, beat_at=_t(2))
    stores.usage.record_usage(
        lease_id=lease_1,
        chunk_id=chunk_1,
        node_id=node_a,
        epoch=1,
        generation=1,
        sample=UsageSample(
            kind="spawn",
            model="model-a",
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=0.01,
        ),
        recorded_at=_t(3),
    )
    stores.usage.record_context_sample(
        lease_id=lease_1, chunk_id=chunk_1, session_id="sess-1", context_tokens=500, sampled_at=_t(3)
    )
    stores.session.record_session_end(lease_id=lease_1, ended_at=_t(4))
    stores.lease_record.record_closure(
        lease_id=lease_1, chunk_id=chunk_1, node_id=node_a, reason="transitioned", closed_at=_t(5)
    )

    stores.environments.record_binding(chunk_id=chunk_1, environment_id="env-1", workdir="/ws/env-1", bound_at=_t(6))
    stores.tokens.set_route_token(chunk_1, token="route-token-1", at=_t(6))

    stores.lease_record.record_lease(
        NewLease(
            lease_id=lease_2,
            chunk_id=chunk_1,
            graph_id=GRAPH_ID,
            node_id=node_a,
            node_name="build",
            epoch=2,
            runner_id=RUNNER_ID,
            retries_max=3,
            created_at=_t(7),
            session_name="pool-a",
            resolved_model="model-b",
            resolved_effort="high",
        )
    )
    # Same session id as lease_1: a resume, which finalizes lease_1's still-open segment
    # and opens a fresh one for lease_2 (``LeaseLivenessStore.record_spawn``'s own D1).
    stores.liveness.record_spawn(lease_2, pid=200, process_start_time="st-200", session_id="sess-1", spawned_at=_t(8))
    stores.liveness.record_heartbeat(lease_id=lease_2, beat_at=_t(9))
    stores.usage.record_usage(
        lease_id=lease_2,
        chunk_id=chunk_1,
        node_id=node_a,
        epoch=2,
        generation=2,
        sample=UsageSample(
            kind="resume",
            model="model-b",
            input_tokens=15,
            output_tokens=25,
            cache_read_tokens=5,
            cache_create_tokens=0,
            cost_usd=0.02,
        ),
        recorded_at=_t(9),
    )
    stores.session.record_session_preamble(
        "sess-1", fingerprint=PreambleFingerprint(blizzard="digest-b", workspace="digest-w"), at=_t(9)
    )
    stores.attachments.record_attachment(
        lease_id=lease_2,
        chunk_id=chunk_1,
        node_id=node_a,
        epoch=2,
        name="asset-a",
        content="content-a",
        attached_at=_t(10),
    )
    stores.attachments.record_attachment(
        lease_id=lease_2,
        chunk_id=chunk_1,
        node_id=node_a,
        epoch=2,
        name="asset-b",
        content="content-b",
        attached_at=_t(10),
    )
    stores.git_commit_declarations.record_git_commit_declaration(
        lease_id=lease_2,
        chunk_id=chunk_1,
        node_id=node_a,
        epoch=2,
        environment_id="env-1",
        repo="repoA",
        branch="main",
        commit="abc123",
        declared_at=_t(11),
    )
    stores.git_commit_declarations.record_git_commit_declaration(
        lease_id=lease_2,
        chunk_id=chunk_1,
        node_id=node_a,
        epoch=2,
        environment_id="env-1",
        repo="repoB",
        branch="main",
        commit="def456",
        declared_at=_t(11),
    )
    stores.checks.record_check_results(
        lease_id=lease_2,
        chunk_id=chunk_1,
        node_id=node_a,
        epoch=2,
        results=[
            CheckResultRecord(command="pytest", passed=True, output_tail="ok"),
            CheckResultRecord(command="lint", passed=False, output_tail="fail"),
        ],
        at=_t(12),
    )
    stores.checks.record_checks_ran(lease_id=lease_2, epoch=2, at=_t(12))
    stores.checks.record_nudge_fired(lease_id=lease_2, epoch=2, at=_t(12))
    stores.asks.record_ask(
        lease_id=lease_2,
        chunk_id=chunk_1,
        question_id="qn_open1",
        question="need input?",
        options=["yes", "no"],
        session_id="sess-1",
        asked_at=_t(13),
    )
    stores.tokens.record_lease_token(lease_2, "hash-lease2", _t(13))

    segments = stores.transcript_ledger.transcript_segments_for_chunk(chunk_1)
    open_segment = next(s for s in segments if s.finalized_at is None)
    stores.transcript_ledger.record_transcript_deltas(
        segment_id=open_segment.segment_id,
        chunk_id=chunk_1,
        cursor="cur-1",
        shipped_bytes=100,
        shipped_turns=2,
        normalizer_version="v1",
        harness_version="h1",
        payloads=['{"turn": 1}', '{"turn": 2}'],
        created_at=_t(14),
    )

    # --- chunk 2: an ask parked (no resume) -------------------------------------------
    lease_3 = "lease_2a"
    stores.lease_record.record_lease(
        NewLease(
            lease_id=lease_3,
            chunk_id=chunk_2,
            graph_id=GRAPH_ID,
            node_id=node_b,
            node_name="review",
            epoch=1,
            runner_id=RUNNER_ID,
            retries_max=2,
            created_at=_t(20),
        )
    )
    stores.liveness.record_spawn(lease_3, pid=300, process_start_time="st-300", session_id="sess-2", spawned_at=_t(21))
    stores.environments.record_binding(chunk_id=chunk_2, environment_id="env-2", workdir="/ws/env-2", bound_at=_t(21))
    stores.asks.record_ask(
        lease_id=lease_3,
        chunk_id=chunk_2,
        question_id="qn_parked1",
        question="parked?",
        options=[],
        session_id="sess-2",
        asked_at=_t(22),
    )
    stores.asks.record_park(lease_id=lease_3, chunk_id=chunk_2, question_id="qn_parked1", parked_at=_t(23))

    # --- chunk 3: an open escalation ---------------------------------------------------
    lease_4 = "lease_3a"
    stores.lease_record.record_lease(
        NewLease(
            lease_id=lease_4,
            chunk_id=chunk_3,
            graph_id=GRAPH_ID,
            node_id=node_c,
            node_name="judge",
            epoch=1,
            runner_id=RUNNER_ID,
            retries_max=2,
            created_at=_t(30),
        )
    )
    stores.lease_record.record_closure(
        lease_id=lease_4, chunk_id=chunk_3, node_id=node_c, reason="escalated", closed_at=_t(31)
    )

    # --- chunk 4: an open takeover ------------------------------------------------------
    lease_5 = "lease_4a"
    stores.lease_record.record_lease(
        NewLease(
            lease_id=lease_5,
            chunk_id=chunk_4,
            graph_id=GRAPH_ID,
            node_id=node_d,
            node_name="build",
            epoch=1,
            runner_id=RUNNER_ID,
            retries_max=2,
            created_at=_t(40),
            session_name="pool-d",
        )
    )
    stores.liveness.record_spawn(lease_5, pid=400, process_start_time="st-400", session_id="sess-4", spawned_at=_t(41))
    stores.takeover.record_takeover(
        takeover_id="tko_1",
        chunk_id=chunk_4,
        lease_id=lease_5,
        session_id="sess-4",
        workdir="/ws/env-4",
        fence_epoch=None,
        opened_at=_t(42),
    )

    # --- chunk 5: a local requeue, no later lease minted --------------------------------
    lease_6 = "lease_5a"
    stores.lease_record.record_lease(
        NewLease(
            lease_id=lease_6,
            chunk_id=chunk_5,
            graph_id=GRAPH_ID,
            node_id=node_e,
            node_name="judge",
            epoch=1,
            runner_id=RUNNER_ID,
            retries_max=2,
            created_at=_t(50),
        )
    )
    stores.lease_record.record_closure(
        lease_id=lease_6, chunk_id=chunk_5, node_id=node_e, reason="escalated", closed_at=_t(51)
    )
    stores.requeue.record_requeue(chunk_id=chunk_5, at=_t(52))

    # --- chunk 6: an in-flight elicitation ----------------------------------------------
    lease_7 = "lease_6a"
    stores.lease_record.record_lease(
        NewLease(
            lease_id=lease_7,
            chunk_id=chunk_6,
            graph_id=GRAPH_ID,
            node_id=node_f,
            node_name="build",
            epoch=1,
            runner_id=RUNNER_ID,
            retries_max=2,
            created_at=_t(60),
        )
    )
    stores.elicitations.record_elicitation_launch(lease_7, 1, output_path="/tmp/elicit-1", at=_t(61))
    stores.elicitations.record_elicitation_started(lease_7, 1, pid=500, process_start_time="st-500")

    # --- chunk 7: an operator pause-park --------------------------------------------------
    lease_8 = "lease_7a"
    stores.lease_record.record_lease(
        NewLease(
            lease_id=lease_8,
            chunk_id=chunk_7,
            graph_id=GRAPH_ID,
            node_id=node_g,
            node_name="build",
            epoch=1,
            runner_id=RUNNER_ID,
            retries_max=2,
            created_at=_t(70),
        )
    )
    stores.pause.record_pause_park(lease_id=lease_8, chunk_id=chunk_7, parked_at=_t(71))

    # --- runner-wide facts, not chunk-keyed ----------------------------------------------
    stores.pause.record_daemon_liveness(runner_id=RUNNER_ID, alive_at=_t(80))
    stores.pause.set_hub_paused(RUNNER_ID, paused=False, at=_t(81))
    stores.pause.record_local_pause(
        RUNNER_ID, paused=False, at=_t(82), by="operator", report_kind="runner.locally_resumed", report_payload="{}"
    )
    stores.resume_intent.record_resume_intent(lease_id=lease_8, marked_at=_t(83))
    workspace_id = "ws_1"
    stores.workspace_prompt.set_workspace_prompt(workspace_id, prompt="hello", at=_t(84))
    stores.graph_artifacts.record_graph_artifacts(
        graph_id=GRAPH_ID,
        artifacts=[
            GraphArtifactRecord(name="a1", ordinal=0, kind=ArtifactKind.ASSET, content="c1"),
            GraphArtifactRecord(name="a2", ordinal=1, kind=ArtifactKind.GIT_COMMIT, content="c2"),
        ],
        recorded_at=_t(85),
    )
    usage_slug = "anthropic"
    stores.usage.record_external_usage_attempt(
        slug=usage_slug, sampled_at=_t(86), payload="{}", report_kind="", report_payload=""
    )

    seq_a = stores.outbound.enqueue_outbound(
        kind="lease.minted", chunk_id=chunk_1, lease_id=lease_2, payload="{}", created_at=_t(90)
    )
    stores.outbound.enqueue_outbound(
        kind="completion.submitted", chunk_id=chunk_1, lease_id=lease_2, payload="{}", created_at=_t(91)
    )
    stores.outbound.ack_outbound(seq_a, acked_at=_t(92))

    read = RunnerReadStores.of(stores)
    return RunnerWorld(
        engine=engine,
        stores=stores,
        read=read,
        chunk_1=chunk_1,
        chunk_2=chunk_2,
        chunk_3=chunk_3,
        chunk_4=chunk_4,
        chunk_5=chunk_5,
        chunk_6=chunk_6,
        chunk_7=chunk_7,
        node_a=node_a,
        lease_1=lease_1,
        lease_2=lease_2,
        lease_3=lease_3,
        lease_5=lease_5,
        lease_7=lease_7,
        lease_8=lease_8,
        session_1="sess-1",
        transcript_segment_id=open_segment.segment_id,
        workspace_id=workspace_id,
        usage_slug=usage_slug,
    )


RunnerRecipe = Callable[[RunnerWorld], object]

#: Every reflected ``(Protocol, method)`` the runner's ``IReadRunnerStore`` umbrella
#: composes (plus each concept protocol it does not), each mapped to a recipe run
#: against :func:`build_runner_world`'s single seeded world.
RUNNER_CENSUS: dict[tuple[type, str], RunnerRecipe] = {
    (IReadLeaseRecordRepository, "list_active_leases"): lambda w: w.read.lease_record.list_active_leases(),
    (IReadLeaseRecordRepository, "active_lease_for_chunk"): lambda w: w.read.lease_record.active_lease_for_chunk(
        w.chunk_1
    ),
    (IReadLeaseRecordRepository, "active_lease"): lambda w: w.read.lease_record.active_lease(w.lease_2),
    (IReadLeaseRecordRepository, "latest_lease_for_chunk"): lambda w: w.read.lease_record.latest_lease_for_chunk(
        w.chunk_1
    ),
    (IReadLeaseRecordRepository, "lease"): lambda w: w.read.lease_record.lease(w.lease_1),
    (IReadLeaseRecordRepository, "list_closed_leases"): lambda w: w.read.lease_record.list_closed_leases(10),
    (IReadLeaseRecordRepository, "attempt_count"): lambda w: w.read.lease_record.attempt_count(w.chunk_1, w.node_a),
    (IReadLeaseRecordRepository, "latest_epoch"): lambda w: w.read.lease_record.latest_epoch(w.chunk_1),
    (IReadLeaseRecordRepository, "lease_ids_for_chunk"): lambda w: w.read.lease_record.lease_ids_for_chunk(w.chunk_1),
    (IReadLeaseSessionRepository, "latest_session_id"): lambda w: w.read.session.latest_session_id(w.chunk_1, None),
    (IReadLeaseSessionRepository, "pool_head"): lambda w: w.read.session.pool_head(w.chunk_1, "pool-a"),
    (IReadLeaseSessionRepository, "session_invocation_count"): lambda w: w.read.session.session_invocation_count(
        w.session_1
    ),
    (IReadLeaseSessionRepository, "lease_for_session"): lambda w: w.read.session.lease_for_session(w.session_1),
    (IReadLeaseSessionRepository, "session_ended_lease_ids"): lambda w: w.read.session.session_ended_lease_ids(),
    (IReadLeaseSessionRepository, "session_preamble_fingerprint"): lambda w: (
        w.read.session.session_preamble_fingerprint(w.session_1)
    ),
    (IReadLeaseLivenessRepository, "latest_heartbeat"): lambda w: w.read.liveness.latest_heartbeat(w.lease_2),
    (IReadLeaseLivenessRepository, "latest_spawn"): lambda w: w.read.liveness.latest_spawn(w.lease_2),
    (IReadLeaseLivenessRepository, "lease_generation"): lambda w: w.read.liveness.lease_generation(w.lease_2),
    (IReadLeaseResumeIntentRepository, "resume_intent_lease_ids"): lambda w: (
        w.read.resume_intent.resume_intent_lease_ids()
    ),
    (IReadEnvironmentRepository, "held_environment_ids"): lambda w: w.read.environments.held_environment_ids(),
    (IReadEnvironmentRepository, "bindings_for_chunk"): lambda w: w.read.environments.bindings_for_chunk(w.chunk_1),
    (IReadEnvironmentRepository, "live_tenure_chunk_ids"): lambda w: w.read.environments.live_tenure_chunk_ids(),
    (IReadEnvironmentRepository, "held_bindings"): lambda w: w.read.environments.held_bindings(),
    (IReadTranscriptLedgerRepository, "transcript_segment"): lambda w: w.read.transcript_ledger.transcript_segment(
        w.transcript_segment_id
    ),
    (IReadTranscriptLedgerRepository, "open_transcript_segments"): lambda w: (
        w.read.transcript_ledger.open_transcript_segments()
    ),
    (IReadTranscriptLedgerRepository, "transcript_segments_for_chunk"): lambda w: (
        w.read.transcript_ledger.transcript_segments_for_chunk(w.chunk_1)
    ),
    (IReadTranscriptLedgerRepository, "chunk_transcript_shipped_bytes"): lambda w: (
        w.read.transcript_ledger.chunk_transcript_shipped_bytes(w.chunk_1)
    ),
    (IReadTranscriptLedgerRepository, "outstanding_transcript_buffer_bytes"): lambda w: (
        w.read.transcript_ledger.outstanding_transcript_buffer_bytes()
    ),
    (IReadTranscriptLedgerRepository, "has_unshipped_transcript_content"): lambda w: (
        w.read.transcript_ledger.has_unshipped_transcript_content(w.chunk_1)
    ),
    (IReadTranscriptLedgerRepository, "pending_transcript_outbound"): lambda w: (
        w.read.transcript_ledger.pending_transcript_outbound()
    ),
    (IReadTranscriptLedgerRepository, "transcript_backfill_leases"): lambda w: (
        w.read.transcript_ledger.transcript_backfill_leases()
    ),
    (IReadTokenRepository, "route_token"): lambda w: w.read.tokens.route_token(w.chunk_1),
    (IReadTokenRepository, "lease_token_hash"): lambda w: w.read.tokens.lease_token_hash(w.lease_2),
    (IReadWorkspacePromptRepository, "workspace_prompt_override"): lambda w: (
        w.read.workspace_prompt.workspace_prompt_override(w.workspace_id)
    ),
    (IReadOutboundRepository, "pending_submission_lease_ids"): lambda w: w.read.outbound.pending_submission_lease_ids(),
    (IReadOutboundRepository, "pending_outbound"): lambda w: w.read.outbound.pending_outbound(),
    (IReadOutboundRepository, "pending_outbound_count"): lambda w: w.read.outbound.pending_outbound_count(),
    (IReadOutboundRepository, "recent_outbound"): lambda w: w.read.outbound.recent_outbound(10),
    (IReadAskRepository, "unforwarded_ask"): lambda w: w.read.asks.unforwarded_ask(w.lease_2),
    (IReadAskRepository, "parked_lease_ids"): lambda w: w.read.asks.parked_lease_ids(),
    (IReadAskRepository, "ask_parked_lease_ids"): lambda w: w.read.asks.ask_parked_lease_ids(),
    (IReadAskRepository, "open_park"): lambda w: w.read.asks.open_park(w.lease_3),
    (IReadAskRepository, "open_asks"): lambda w: w.read.asks.open_asks(),
    (IReadPauseRepository, "hub_contact_at"): lambda w: w.read.pause.hub_contact_at(RUNNER_ID),
    (IReadPauseRepository, "hub_paused"): lambda w: w.read.pause.hub_paused(RUNNER_ID),
    (IReadPauseRepository, "local_paused"): lambda w: w.read.pause.local_paused(RUNNER_ID),
    (IReadPauseRepository, "last_daemon_liveness"): lambda w: w.read.pause.last_daemon_liveness(),
    (IReadPauseRepository, "pause_parked_lease_ids"): lambda w: w.read.pause.pause_parked_lease_ids(),
    (IReadTakeoverRepository, "lease_for_open_takeover"): lambda w: w.read.takeover.lease_for_open_takeover(w.lease_5),
    (IReadTakeoverRepository, "open_takeover_for_chunk"): lambda w: w.read.takeover.open_takeover_for_chunk(w.chunk_4),
    (IReadTakeoverRepository, "open_takeover_chunk_ids"): lambda w: w.read.takeover.open_takeover_chunk_ids(),
    (IReadTakeoverRepository, "open_takeovers"): lambda w: w.read.takeover.open_takeovers(),
    (IReadRequeueRepository, "pending_requeue_chunk_ids"): lambda w: w.read.requeue.pending_requeue_chunk_ids(),
    (IReadEscalationRepository, "open_escalations"): lambda w: w.read.escalations.open_escalations(),
    (IReadEscalationRepository, "open_escalation_for_chunk"): lambda w: w.read.escalations.open_escalation_for_chunk(
        w.chunk_3
    ),
    (IReadUsageRepository, "usage_since"): lambda w: w.read.usage.usage_since(_BASE),
    (IReadUsageRepository, "context_sample_state"): lambda w: w.read.usage.context_sample_state(w.lease_1),
    (IReadUsageRepository, "last_external_usage_attempt_at"): lambda w: w.read.usage.last_external_usage_attempt_at(
        w.usage_slug
    ),
    (IReadAttachmentRepository, "attachments_for_lease"): lambda w: w.read.attachments.attachments_for_lease(w.lease_2),
    (IReadAttachmentRepository, "attachment_names_for_lease"): lambda w: w.read.attachments.attachment_names_for_lease(
        w.lease_2
    ),
    (IReadGitCommitDeclarationRepository, "git_commit_declarations_for_lease"): lambda w: (
        w.read.git_commit_declarations.git_commit_declarations_for_lease(w.lease_2)
    ),
    (IReadCheckRepository, "nudge_fired"): lambda w: w.read.checks.nudge_fired(w.lease_2, 2),
    (IReadCheckRepository, "checks_ran"): lambda w: w.read.checks.checks_ran(w.lease_2, 2),
    (IReadCheckRepository, "check_results_for_lease"): lambda w: w.read.checks.check_results_for_lease(w.lease_2, 2),
    (IReadGraphArtifactRepository, "graph_artifacts_for_graph"): lambda w: (
        w.read.graph_artifacts.graph_artifacts_for_graph(GRAPH_ID)
    ),
    (IReadElicitationRepository, "in_flight_elicitation"): lambda w: w.read.elicitations.in_flight_elicitation(
        w.lease_7, 1
    ),
    (IReadElicitationRepository, "in_flight_elicitation_lease_ids"): lambda w: (
        w.read.elicitations.in_flight_elicitation_lease_ids()
    ),
}

#: Runner ``IRead*`` methods with no SQL behind them at all — an httpx hub client and a
#: harness filesystem source, neither wired through :func:`build_runner_world`'s store
#: bundle, so no recipe could drive either through it.
RUNNER_EXEMPTIONS: dict[tuple[type, str], str] = {
    (IReadArchivedTranscriptRepository, "read_turns"): (
        "blizzard.runner.transcripts.internal.http_archived_transcript_repository."
        "HttpArchivedTranscriptRepository implements this over httpx against the hub's "
        "HTTP API — no SQL, and not part of blizzard.runner.composition.build_stores's "
        "bundle."
    ),
    (IReadTranscriptRepository, "read_turns"): (
        "blizzard.runner.transcripts.internal.projected_transcript_repository."
        "ProjectedTranscriptRepository implements this over IHarnessTranscriptSource, a "
        "filesystem read of the harness's own session directory — no SQL, and not part of "
        "blizzard.runner.composition.build_stores's bundle."
    ),
}
