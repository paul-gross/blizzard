"""Read-method census for the store-read-index gate (blizzard#525).

Maps every reflected ``IRead*`` Protocol method to a recipe that exercises it against a production-wired,
migrated-to-head store, seeded once per gate run entirely through each concept's own write Protocol
(``bzh:matrix-tier-rules``), never raw SQL. Split by store (``RUNNER_CENSUS``/``RUNNER_EXEMPTIONS`` and
``HUB_CENSUS``/``HUB_EXEMPTIONS``) so each store's own census reads and edits independently of the other's."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine

from blizzard.auth_core import Role
from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.auth.auth_state import IReadAuthStateRepository
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.facts import IReadAuthFactsRepository
from blizzard.hub.auth.identities import IReadIdentityRepository
from blizzard.hub.auth.internal.auth_facts_repository import AuthFactsRepository
from blizzard.hub.auth.internal.auth_state_repository import AuthStateRepository
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.superuser_bootstrap_repository import SuperuserBootstrapRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.models import AuthStateEntry, Identity, SuperuserBootstrap, User
from blizzard.hub.auth.sessions import IReadSessionRepository
from blizzard.hub.auth.superuser_bootstrap import IReadSuperuserBootstrapRepository
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.cli.sessions import IReadSessionStore
from blizzard.hub.domain.analytics.events import IReadTranscriptEvents
from blizzard.hub.domain.analytics.extraction import EXTRACTOR_VERSION
from blizzard.hub.domain.analytics.operational import IReadOperationalAnalytics, OperationalCriteria
from blizzard.hub.domain.analytics.queries import EventQueryCriteria, IReadAnalyticsEventQueries
from blizzard.hub.domain.chunks.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunks.decisions import IReadChunkDecisionsRepository
from blizzard.hub.domain.chunks.delivery import IReadChunkDeliveryRepository
from blizzard.hub.domain.chunks.dependencies import IReadChunkDependenciesRepository
from blizzard.hub.domain.chunks.escalations import IReadChunkEscalationsRepository
from blizzard.hub.domain.chunks.events import IReadChunkEventsRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.hub_exec import IReadChunkHubExecRepository
from blizzard.hub.domain.chunks.lifecycle import IReadChunkLifecycleRepository
from blizzard.hub.domain.chunks.movement import IReadChunkMovementRepository
from blizzard.hub.domain.chunks.questions import IReadChunkQuestionsRepository
from blizzard.hub.domain.chunks.queue import IReadChunkQueueRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunks.route import IReadChunkRouteRepository
from blizzard.hub.domain.chunks.stores import ChunkReadStores, ChunkStores
from blizzard.hub.domain.chunks.usage import IReadChunkUsageRepository
from blizzard.hub.domain.chunks.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.findings import IReadFindingRepository, IReadFindingSetRepository
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.garden_proposal_closure import IReadGardenProposalClosureRepository
from blizzard.hub.domain.garden_proposal_resolution import resolve_proposal_findings
from blizzard.hub.domain.garden_proposals import IReadGardenProposalRepository
from blizzard.hub.domain.garden_run import IReadGardenRunRepository
from blizzard.hub.domain.garden_sweeps import IReadGardenSweepsRepository
from blizzard.hub.domain.garden_trend import IReadGardenTrendRepository
from blizzard.hub.domain.graph import Graph, IReadGraphRepository
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.registry import IReadRunnerRegistry
from blizzard.hub.domain.routines import IReadRoutineRepository, IReadRoutineScopeRepository, RunMode
from blizzard.hub.domain.run_context import IReadRunContextRepository
from blizzard.hub.domain.scopes import IReadScopeRepository, ScopeSlug
from blizzard.hub.domain.transcripts import IReadTranscriptSegments
from blizzard.hub.domain.work import (
    Chunk,
    DecisionChoice,
    IReadWorkItemRepository,
    MigrationSource,
    WorkItemAuthor,
    WorkRef,
)
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import MARKER_PREFIX
from blizzard.hub.store.internal.finding_store import FindingSetStore, FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.garden_run_store import GardenRunStore
from blizzard.hub.store.internal.garden_sweeps_store import GardenSweepsStore
from blizzard.hub.store.internal.garden_trend_store import GardenTrendStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
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
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.harness.workspace_prompts import IReadWorkspacePromptRepository
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.stores import RunnerReadStores, RunnerStores
from blizzard.runner.transcripts.archived_repository import IReadArchivedTranscriptRepository
from blizzard.runner.transcripts.ledger import IReadTranscriptLedgerRepository
from blizzard.runner.transcripts.repository import IReadTranscriptRepository
from tests.support import HubHarness, build_hub, chunk_stores, hub_store_connections, seed_work_item

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
    (:func:`build_runner_world`) and driven by every recipe in :data:`RUNNER_CENSUS`. ``stores``/``read`` are the
    same production-wired adapters, the latter narrowed to ``RunnerReadStores`` (D1) — recipes read through
    ``read``, the one collaborator every controller-facing caller resolves through in production."""

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
    session_1: SessionReference
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
    stores.liveness.record_spawn(
        lease_1,
        pid=100,
        process_start_time="st-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1"),
        spawned_at=_t(1),
    )
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
        lease_id=lease_1,
        chunk_id=chunk_1,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1"),
        context_tokens=500,
        sampled_at=_t(3),
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
    stores.liveness.record_spawn(
        lease_2,
        pid=200,
        process_start_time="st-200",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1"),
        spawned_at=_t(8),
    )
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
        SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1"),
        fingerprint=PreambleFingerprint(blizzard="digest-b", workspace="digest-w"),
        at=_t(9),
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
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1"),
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
    stores.liveness.record_spawn(
        lease_3,
        pid=300,
        process_start_time="st-300",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-2"),
        spawned_at=_t(21),
    )
    stores.environments.record_binding(chunk_id=chunk_2, environment_id="env-2", workdir="/ws/env-2", bound_at=_t(21))
    stores.asks.record_ask(
        lease_id=lease_3,
        chunk_id=chunk_2,
        question_id="qn_parked1",
        question="parked?",
        options=[],
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-2"),
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
    stores.liveness.record_spawn(
        lease_5,
        pid=400,
        process_start_time="st-400",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-4"),
        spawned_at=_t(41),
    )
    stores.takeover.record_takeover(
        takeover_id="tko_1",
        chunk_id=chunk_4,
        lease_id=lease_5,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-4"),
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
        session_1=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1"),
        transcript_segment_id=open_segment.segment_id,
        workspace_id=workspace_id,
        usage_slug=usage_slug,
    )


RunnerRecipe = Callable[[RunnerWorld], object]

#: Every reflected runner ``(Protocol, method)``, mapped to a recipe run against :func:`build_runner_world`'s world.
RUNNER_CENSUS: dict[tuple[type, str], RunnerRecipe] = {
    (IReadLeaseRecordRepository, "list_active_leases"): lambda w: w.read.lease_record.list_active_leases(),
    (IReadLeaseRecordRepository, "active_lease_for_chunk"): lambda w: w.read.lease_record.active_lease_for_chunk(
        w.chunk_1
    ),
    (IReadLeaseRecordRepository, "active_lease"): lambda w: w.read.lease_record.active_lease(w.lease_2),
    (IReadLeaseRecordRepository, "latest_lease_for_chunk"): lambda w: w.read.lease_record.latest_lease_for_chunk(
        w.chunk_1
    ),
    (IReadLeaseRecordRepository, "latest_lease_with_session_for_chunk"): lambda w: (
        w.read.lease_record.latest_lease_with_session_for_chunk(w.chunk_1)
    ),
    (IReadLeaseRecordRepository, "lease"): lambda w: w.read.lease_record.lease(w.lease_1),
    (IReadLeaseRecordRepository, "list_closed_leases"): lambda w: w.read.lease_record.list_closed_leases(10),
    (IReadLeaseRecordRepository, "attempt_count"): lambda w: w.read.lease_record.attempt_count(w.chunk_1, w.node_a),
    (IReadLeaseRecordRepository, "latest_epoch"): lambda w: w.read.lease_record.latest_epoch(w.chunk_1),
    (IReadLeaseRecordRepository, "lease_ids_for_chunk"): lambda w: w.read.lease_record.lease_ids_for_chunk(w.chunk_1),
    (IReadLeaseSessionRepository, "latest_session"): lambda w: w.read.session.latest_session(w.chunk_1, None),
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

#: Runner ``IRead*`` methods with no SQL behind them at all, each reasoned below.
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


# --- Hub -----------------------------------------------------------------------------------

_HUB_BASE = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)
_HUB_UNTIL = _HUB_BASE + timedelta(days=365)


def _ht(offset_seconds: float) -> datetime:
    """The hub world's own deterministic, strictly-ordered stamp — mirrors :func:`_t`
    (kept separate so the runner and hub worlds' own offsets never have to stay in
    lockstep with one another)."""
    return _HUB_BASE + timedelta(seconds=offset_seconds)


HUB_RUNNER_ID = "r1"
HUB_RUNNER_ID_2 = "r2"

#: A two-node graph (build judged into deliver) minted fresh so every chunk-domain recipe has a real node id to cite.
_HUB_GRAPH_NAME = "hub-gate-graph"
_HUB_GRAPH_YAML = f"""
name: {_HUB_GRAPH_NAME}
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: deliver
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""

_HUB_RETIRED_GRAPH_NAME = "hub-gate-graph-retiree"
_HUB_RETIRED_GRAPH_YAML = _HUB_GRAPH_YAML.replace(_HUB_GRAPH_NAME, _HUB_RETIRED_GRAPH_NAME, 1)


def _tool_turn(index: int, name: str, tool_input: dict[str, object]) -> dict:
    """One transcript tool-call turn shaped for the analytics extractors — mirrors
    ``tests/test_analytics_events_api.py``'s own ``_tool_turn``, the smallest shape the
    file_read/skill_invocation/agent_spawn extractors parse."""
    return {
        "index": index,
        "kind": "tool",
        "timestamp": "2026-07-20T09:00:00Z",
        "text": "",
        "tool": {
            "name": name,
            "input": tool_input,
            "input_unparsed": None,
            "input_shape": "object",
            "tool_use_id": f"t{index}",
            "output": None,
            "output_truncated": False,
        },
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }


@dataclass(frozen=True)
class HubWorld:
    """The hub store's module-scoped seeded world — one instance built once
    (:func:`build_hub_world`) and driven by every recipe in :data:`HUB_CENSUS`. ``hub`` carries the production-wired
    :class:`~tests.support.HubHarness` (D2); ``write`` is a second, write-typed :class:`ChunkStores` instance recipes
    never read through, only seeding does (``hub.services.chunks`` is typed read-only, ``bzh:controller-read-only``)."""

    hub: HubHarness
    engine: Engine
    clock: FixedClock
    store_connections: HubStoreConnections
    write: ChunkStores
    read: ChunkReadStores
    auth_state: IReadAuthStateRepository
    auth_facts: IReadAuthFactsRepository
    superuser_bootstrap: IReadSuperuserBootstrapRepository
    transcript_events: IReadTranscriptEvents
    garden_run: IReadGardenRunRepository
    garden_sweeps: IReadGardenSweepsRepository
    garden_trend: IReadGardenTrendRepository
    work_items: IReadWorkItemRepository
    default_graph: Graph
    graph: Graph
    retired_graph: Graph
    build_node_id: str
    deliver_node_id: str
    user_1: User
    user_2: User
    session_id_hash: str
    auth_state_value: str
    runner_token_hash: str
    routine_id: str
    scope_a: str
    scope_c: str
    finding_1: str
    finding_2: str
    garden_proposal_1: str
    garden_proposal_2: str
    materialized_source: str
    materialized_ref: str
    work_item_ref_1: str
    work_item_ref_2: str
    run_chunk_1: str
    run_chunk_1_chunk: Chunk
    chunk_ready_1: str
    chunk_ready_2: str
    chunk_transition: str
    chunk_migration: str
    chunk_decision_1: str
    chunk_decision_2: str
    question_1: str
    chunk_question: str
    chunk_artifacts: str
    chunk_delivery_a: str
    chunk_dependency_dependent: str
    chunk_dependency_prerequisite: str
    chunk_lifecycle_merged: str
    chunk_route_a: str
    chunk_route_b: str
    transcript_chunk: str
    transcript_segment_id: str


def build_hub_world(tmp_path: Path) -> HubWorld:
    """Seed a migrated-to-head hub store through its own write Protocols and production services, so every read
    method in :data:`HUB_CENSUS` reaches real, non-empty behavior. Every id below is a plain literal — the
    store enforces no foreign key (``create_engine_from_url`` never turns ``PRAGMA foreign_keys`` on), so a fact's
    own node id only has to be real where a fact family's OWN read resolves it against the graph, never where a
    chunk-fact seam merely carries it."""
    hub = build_hub(tmp_path)
    engine = hub.engine
    clock = hub.clock
    store_connections = hub_store_connections(engine)
    write = chunk_stores(engine, clock)
    read = hub.services.chunks
    auth_errors = RepoErrorFactory(get_logger("test"))

    auth_state_store = AuthStateRepository(store_connections, auth_errors)
    auth_facts_store = AuthFactsRepository(store_connections)
    superuser_bootstrap_store = SuperuserBootstrapRepository(store_connections)
    identity_store = IdentityRepository(store_connections, auth_errors)
    transcript_events = TranscriptEventStore(store_connections)
    garden_run_store = GardenRunStore(store_connections)
    garden_sweeps_store = GardenSweepsStore(store_connections)
    garden_trend_store = GardenTrendStore(store_connections)
    work_items = WorkItemStore(store_connections)
    finding_store = FindingStore(store_connections)
    finding_set_store = FindingSetStore(store_connections)
    garden_proposal_store = GardenProposalStore(store_connections)
    user_store = UserRepository(store_connections, auth_errors)

    def _mint(chunk_id: str, ref: str, *, at: datetime) -> None:
        assert graph is not None
        write.record.mint(
            Chunk(
                chunk_id=chunk_id, graph_id=graph.graph_id, work_refs=[WorkRef(source="default", ref=ref)], minted_at=at
            )
        )

    # --- graphs -------------------------------------------------------------------------
    default_graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _HUB_GRAPH_YAML})
    assert minted.status_code == 201, minted.text
    graph = hub.services.graphs.get_enabled_by_name(_HUB_GRAPH_NAME)
    assert graph is not None
    hub.services.graph_lifecycle.set_follow_latest(graph, follow_latest=True, by="operator")
    minted_retiree = hub.client.post("/api/graphs", json={"definition_yaml": _HUB_RETIRED_GRAPH_YAML})
    assert minted_retiree.status_code == 201, minted_retiree.text
    retired_graph = hub.services.graphs.get_enabled_by_name(_HUB_RETIRED_GRAPH_NAME)
    assert retired_graph is not None
    hub.services.graph_lifecycle.retire(retired_graph, by="operator")
    build_node = next(n for n in graph.nodes if n.name == "build")
    deliver_node = next(n for n in graph.nodes if n.name == "deliver")

    # --- auth: users, sessions, identities, auth-state, auth-facts, superuser bootstrap --
    user_1 = User(
        user_id="usr_hub_1",
        username="alice",
        display_name="Alice",
        email="alice@example.com",
        role=Role.CONTRIBUTOR,
        created_at=_ht(0),
    )
    user_2 = User(
        user_id="usr_hub_2",
        username="bob",
        display_name="Bob",
        email="bob@example.com",
        role=Role.ADMIN,
        created_at=_ht(1),
    )
    user_store.create(user_1)
    user_store.create(user_2)

    _plaintext, session_1 = hub.services.auth.mint_session(user_1)
    session_id_hash = session_1.id_hash

    identity_store.link(
        Identity(provider_name="github", subject="12345", user_id=user_1.user_id, handle="alice-gh", created_at=_ht(2))
    )
    identity_store.link(
        Identity(provider_name="github", subject="67890", user_id=user_2.user_id, handle="bob-gh", created_at=_ht(3))
    )

    auth_state_value = "state_hub_1"
    auth_state_store.create(
        AuthStateEntry(
            state=auth_state_value,
            kind="login",
            provider_name="github",
            return_to="/",
            code_challenge=None,
            created_at=_ht(4),
            expires_at=_ht(304),
        )
    )

    hub.services.auth_facts.login_failed(actor="anon", subject="alice@example.com", detail="bad state")
    hub.services.auth_facts.sso_refused(actor="anon", subject="alice@example.com", detail="wrong provider")

    superuser_bootstrap_store.upsert(
        SuperuserBootstrap(email="root@example.com", claimed_user_id=None, updated_at=_ht(5))
    )

    # --- registry -------------------------------------------------------------------------
    hub.services.fleet.register(
        HUB_RUNNER_ID,
        "workspace-1",
        env_capacity=2,
        public_url="http://r1.local",
        redirect_uris=("http://r1.local/cb",),
    )
    hub.services.fleet.register(HUB_RUNNER_ID_2, "workspace-2")
    registration = hub.services.registry.get_runner(HUB_RUNNER_ID)
    assert registration is not None
    runner_token = hub.services.enrollment.enroll(registration)
    runner_token_hash = TokenHash(runner_token).hex
    hub.services.fleet.set_paused(registration, paused=True, by="operator")
    hub.services.fleet.record_local_pause(HUB_RUNNER_ID_2, paused=True, at=_ht(6), by="runner", reason="disk full")

    # --- scopes and routines ---------------------------------------------------------------
    scope_a = hub.services.scope_registry.ensure(ScopeSlug.parse("blizzard"), description="core")
    scope_b = hub.services.scope_registry.ensure(ScopeSlug.parse("runner-scope"), description="runner side")
    scope_c = hub.services.scope_registry.ensure(ScopeSlug.parse("legacy"), description="retired")
    hub.services.scope_lifecycle.retire(scope_c, by="operator")

    routine = hub.services.routine_authoring.create(
        name="gardening",
        graph_name=default_graph.name,
        default_scope_slug=ScopeSlug.parse("blizzard"),
        default_model=["opus"],
        default_effort="high",
    )
    hub.services.routine_scope_membership.link(routine, scope_b)
    routine_2 = hub.services.routine_authoring.create(
        name="nightly", graph_name=default_graph.name, default_scope_slug=ScopeSlug.parse("runner-scope")
    )

    # --- routine runs: garden_run/run_context's own source ---------------------------------
    statuses = hub.services.chunks.facts.load_all_statuses()
    run_1 = hub.services.routine_run.run(
        routine,
        scope=scope_a,
        mode=RunMode.FULL,
        note=None,
        author=WorkItemAuthor.user(user_1.user_id),
        statuses=statuses,
    )
    statuses = hub.services.chunks.facts.load_all_statuses()
    # A second run is all `runs_in_window`/`list_all` need (>=2 rows) — its own chunk is
    # never referenced by any recipe, so its return value is deliberately discarded.
    hub.services.routine_run.run(
        routine_2,
        scope=scope_b,
        mode=RunMode.FULL,
        note="second run",
        author=WorkItemAuthor.user(user_1.user_id),
        statuses=statuses,
    )
    run_chunk_1 = run_1.chunk_id
    run_chunk_1_chunk = hub.services.chunks.record.get(run_chunk_1)
    assert run_chunk_1_chunk is not None

    # --- findings, finding sets (garden_run/garden_sweeps/garden_trend's own source) -------
    finding_1 = "fin_hub_1"
    finding_store.add(
        finding_1,
        routine_name="gardening",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s1",
        introduced=None,
        at=_ht(10),
    )
    finding_store.record_fact(finding_1, kind="add", at=_ht(10))

    finding_2 = "fin_hub_2"
    finding_store.add(
        finding_2,
        routine_name="gardening",
        scope_slug="blizzard",
        class_="dead-code",
        locus="b.py:2",
        summary="s2",
        introduced=None,
        at=_ht(11),
    )
    finding_store.record_fact(finding_2, kind="add", at=_ht(11))

    garden_proposal_1 = "gprop_hub_1"
    garden_proposal_store.create(
        garden_proposal_1,
        routine_name="gardening",
        class_="remediate",
        title="fix 1",
        body="body 1",
        findings=[finding_1],
        at=_ht(12),
    )
    garden_proposal_2 = "gprop_hub_2"
    garden_proposal_store.create(
        garden_proposal_2,
        routine_name="gardening",
        class_="remediate",
        title="fix 2",
        body="body 2",
        findings=[finding_2],
        at=_ht(13),
    )

    finding_store.record_fact(
        finding_2, kind="resolved", at=_ht(14), note="landed", actor="operator", proposal_id=garden_proposal_2
    )

    proposal_1 = garden_proposal_store.get(garden_proposal_1)
    assert proposal_1 is not None
    hub.services.garden_proposal_closure.pass_(proposal_1, reason="not worth it", by="operator")

    proposal_2 = garden_proposal_store.get(garden_proposal_2)
    assert proposal_2 is not None
    resolved_findings = resolve_proposal_findings(hub.services.findings, proposal_2.findings)
    accepted = hub.services.garden_proposal_closure.accept(
        proposal_2, reason=None, by="operator", body=None, mint=True, graph=default_graph, findings=resolved_findings
    )
    assert accepted.closure.source is not None
    assert accepted.closure.ref is not None
    materialized_source = accepted.closure.source
    materialized_ref = accepted.closure.ref

    # --- delivered finding sets over run_chunk_1's own artifacts ----------------------------
    write.artifacts.record_hub_artifact(
        run_chunk_1,
        node_id="nd_survey",
        node_name="survey",
        epoch=1,
        name="findings",
        content=json.dumps(
            {"scope": "blizzard", "revisions": {"blizzard": "aaa"}, "measurement": None, "findings": []}
        ),
        at=_ht(20),
    )
    artifact_1 = write.artifacts.latest_artifact(run_chunk_1, "findings")
    assert artifact_1 is not None
    finding_set_store.create(
        "fins_hub_1",
        artifact_id=artifact_1.artifact_id,
        chunk_id=run_chunk_1,
        scope_slug="blizzard",
        routine_name="gardening",
        revisions={"blizzard": "aaa"},
        measurement=None,
    )
    write.artifacts.record_hub_artifact(
        run_chunk_1,
        node_id="nd_survey",
        node_name="survey",
        epoch=1,
        name="findings-runner",
        content=json.dumps({"scope": "runner-scope", "revisions": {}, "measurement": None, "findings": []}),
        at=_ht(21),
    )
    artifact_2 = write.artifacts.latest_artifact(run_chunk_1, "findings-runner")
    assert artifact_2 is not None
    finding_set_store.create(
        "fins_hub_2",
        artifact_id=artifact_2.artifact_id,
        chunk_id=run_chunk_1,
        scope_slug="runner-scope",
        routine_name="gardening",
        revisions={},
        measurement=None,
    )

    # --- transcripts + derived analytics events ---------------------------------------------
    transcript_chunk = run_chunk_1
    transcript_segment_id = "seg_hub_1"
    turns = [
        _tool_turn(0, "Read", {"file_path": "src/a.py"}),
        _tool_turn(1, "Skill", {"skill": "wf-commit"}),
        _tool_turn(2, "Agent", {"subagent_type": "explorer"}),
    ]
    record = {
        "seq": 1,
        "segment_id": transcript_segment_id,
        "chunk_id": transcript_chunk,
        "node_id": build_node.node_id,
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": 0,
        "turn_range_end": len(turns) - 1,
        "final": True,
        "normalizer_version": "claude-code-jsonl/2",
        "harness_version": "claude-code-1.0",
        "turns": turns,
    }
    pushed = hub.client.post("/api/fleet/transcripts", json={"runner_id": HUB_RUNNER_ID, "records": [record]})
    assert pushed.status_code == 200, pushed.text
    hub.services.event_derivation.sweep()

    # --- chunk record / queue ----------------------------------------------------------------
    chunk_ready_1 = "ch_hub_ready_1"
    chunk_ready_2 = "ch_hub_ready_2"
    _mint(chunk_ready_1, "1001", at=_ht(30))
    _mint(chunk_ready_2, "1002", at=_ht(31))
    write.queue.record_promote_with_tail_position(chunk_ready_1, position=1.0, at=_ht(32))
    write.queue.record_promote_with_tail_position(chunk_ready_2, position=2.0, at=_ht(33))
    chunk_not_ready = "ch_hub_not_ready"
    _mint(chunk_not_ready, "1003", at=_ht(34))

    # --- movement ------------------------------------------------------------------------------
    chunk_transition = "ch_hub_transition"
    _mint(chunk_transition, "1004", at=_ht(35))
    write.movement.record_transition(
        transition_id="tr_hub_1",
        chunk_id=chunk_transition,
        from_node_id=None,
        to_node_id=build_node.node_id,
        choice_name=None,
        epoch=1,
        runner_id=HUB_RUNNER_ID,
        at=_ht(36),
        artifacts=[],
        proposals=[],
    )
    write.movement.record_transition(
        transition_id="tr_hub_2",
        chunk_id=chunk_transition,
        from_node_id=build_node.node_id,
        to_node_id=deliver_node.node_id,
        choice_name="pass",
        epoch=2,
        runner_id=HUB_RUNNER_ID,
        at=_ht(37),
        artifacts=[],
        proposals=[
            WorkItemProposalRow(
                proposal_id="wip_hub_1",
                chunk_id=chunk_transition,
                node_id=build_node.node_id,
                node_name="build",
                epoch=2,
                ordinal=0,
                kind="create",
                data=json.dumps({"title": "proposed", "body": "b", "stated_priority": None}),
                runner_id=HUB_RUNNER_ID,
            )
        ],
    )

    chunk_migration = "ch_hub_migration"
    _mint(chunk_migration, "1005", at=_ht(40))
    write.movement.record_migration(
        chunk_migration,
        from_node_id=build_node.node_id,
        from_graph_id=graph.graph_id,
        to_graph_id=default_graph.graph_id,
        landed_node_id=None,
        choice_name=None,
        decision_id=None,
        model=None,
        epoch=1,
        at=_ht(41),
        artifacts=[],
        proposals=[],
        source=MigrationSource.AUTHORED_EDGE,
    )

    # --- decisions -----------------------------------------------------------------------------
    chunk_decision_1 = "ch_hub_decision_1"
    chunk_decision_2 = "ch_hub_decision_2"
    _mint(chunk_decision_1, "1006", at=_ht(42))
    _mint(chunk_decision_2, "1007", at=_ht(43))
    write.decisions.record_decision(
        decision_id="dec_hub_1",
        chunk_id=chunk_decision_1,
        node_id=build_node.node_id,
        node_name="build",
        epoch=1,
        choices=[
            DecisionChoice(name="approve", description="do it"),
            DecisionChoice(name="reject", description="don't"),
        ],
        at=_ht(44),
        artifacts=[],
        proposals=[
            WorkItemProposalRow(
                proposal_id="wip_hub_2",
                chunk_id=chunk_decision_1,
                node_id=build_node.node_id,
                node_name="build",
                epoch=1,
                ordinal=0,
                kind="create",
                data=json.dumps({"title": "another proposal", "body": "b", "stated_priority": "p1"}),
                runner_id=HUB_RUNNER_ID,
            )
        ],
    )
    write.decisions.record_decision(
        decision_id="dec_hub_2",
        chunk_id=chunk_decision_2,
        node_id=build_node.node_id,
        node_name="build",
        epoch=1,
        choices=[DecisionChoice(name="approve", description="do it")],
        at=_ht(45),
        artifacts=[],
        proposals=[],
    )

    # --- escalations ---------------------------------------------------------------------------
    chunk_escalation_1 = "ch_hub_escalation_1"
    chunk_escalation_2 = "ch_hub_escalation_2"
    _mint(chunk_escalation_1, "1008", at=_ht(46))
    _mint(chunk_escalation_2, "1009", at=_ht(47))
    write.escalations.record_escalation(
        chunk_escalation_1, epoch=1, takeover_command="resume", at=_ht(48), wrapped_takeover_command="wrapped-resume"
    )
    write.escalations.record_escalation(
        chunk_escalation_2, epoch=1, takeover_command="resume", at=_ht(49), wrapped_takeover_command="wrapped-resume"
    )

    # --- questions -----------------------------------------------------------------------------
    chunk_question = "ch_hub_question"
    _mint(chunk_question, "1010", at=_ht(50))
    question_1 = "qn_hub_1"
    question_2 = "qn_hub_2"
    write.questions.record_question(
        question_id=question_1,
        chunk_id=chunk_question,
        node_id=build_node.node_id,
        session_id="sess-hub-1",
        runner_id=HUB_RUNNER_ID,
        epoch=1,
        question="proceed?",
        options=["yes", "no"],
        asked_at=_ht(51),
    )
    write.questions.record_question(
        question_id=question_2,
        chunk_id=chunk_question,
        node_id=build_node.node_id,
        session_id="sess-hub-1",
        runner_id=HUB_RUNNER_ID,
        epoch=1,
        question="also?",
        options=[],
        asked_at=_ht(52),
    )
    write.questions.answer_question(question_1, answer="yes", answered_by=user_1.user_id, at=_ht(53))
    write.questions.record_answer_delivered(question_id=question_1, chunk_id=chunk_question, at=_ht(54))

    # --- usage ---------------------------------------------------------------------------------
    chunk_usage = "ch_hub_usage"
    _mint(chunk_usage, "1011", at=_ht(55))
    write.usage.record_usage(
        chunk_usage,
        node_id=build_node.node_id,
        epoch=1,
        runner_id=HUB_RUNNER_ID,
        kind="spawn",
        model="model-a",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.01,
        at=_ht(56),
    )
    write.usage.record_usage(
        chunk_usage,
        node_id=build_node.node_id,
        epoch=1,
        runner_id=HUB_RUNNER_ID,
        kind="resume",
        model="model-b",
        input_tokens=20,
        output_tokens=8,
        cache_read_tokens=1,
        cache_create_tokens=0,
        cost_usd=0.02,
        at=_ht(57),
    )

    # --- events --------------------------------------------------------------------------------
    chunk_events = "ch_hub_events"
    _mint(chunk_events, "1012", at=_ht(58))
    write.events.record_event(
        severity="info",
        kind="chunk.transitioned",
        runner_id=HUB_RUNNER_ID,
        chunk_id=chunk_events,
        lease_id=None,
        node_name="build",
        message="moved",
        detail={"a": 1},
        at=_ht(59),
    )
    write.events.record_event(
        severity="warning",
        kind="chunk.paused",
        runner_id=None,
        chunk_id=chunk_events,
        lease_id=None,
        node_name=None,
        message="paused",
        detail=None,
        at=_ht(60),
    )

    # --- hub_exec --------------------------------------------------------------------------------
    chunk_hub_exec_live = "ch_hub_exec_live"
    chunk_hub_exec_released = "ch_hub_exec_released"
    _mint(chunk_hub_exec_live, "1013", at=_ht(61))
    _mint(chunk_hub_exec_released, "1014", at=_ht(62))
    # Released first: the slot is fleet-wide-single, so the still-held one below must be
    # acquired last to stay the sole live holder `count_live_hub_exec_slots` counts.
    write.hub_exec.acquire_hub_exec_slot(
        chunk_hub_exec_released, node_id=deliver_node.node_id, at=_ht(63), stale_after=timedelta(minutes=5)
    )
    write.hub_exec.record_hub_node_poll(chunk_hub_exec_released, node_id=deliver_node.node_id, epoch=1, at=_ht(64))
    write.hub_exec.record_hub_step_transition(
        chunk_hub_exec_released,
        from_node_id=deliver_node.node_id,
        to_node_id="done",
        choice_name="success",
        epoch=1,
        runner_id="hub",
        transition_id="tr_hub_exec_1",
        at=_ht(65),
        artifacts=[],
        release_route=True,
    )
    write.hub_exec.release_hub_exec_slot(chunk_hub_exec_released, at=_ht(66))
    write.hub_exec.acquire_hub_exec_slot(
        chunk_hub_exec_live, node_id=deliver_node.node_id, at=_ht(67), stale_after=timedelta(minutes=5)
    )
    write.hub_exec.record_hub_node_poll(chunk_hub_exec_live, node_id=deliver_node.node_id, epoch=1, at=_ht(68))

    # --- delivery + close intents -----------------------------------------------------------------
    chunk_delivery_a = "ch_hub_delivery_a"
    chunk_delivery_b = "ch_hub_delivery_b"
    _mint(chunk_delivery_a, "1015", at=_ht(69))
    _mint(chunk_delivery_b, "1016", at=_ht(70))
    write.delivery.record_delivery_repo_landed(chunk_delivery_a, repo="acme/widget", commit_hash="c1", at=_ht(71))
    write.delivery.record_delivery_repo_landed(chunk_delivery_a, repo="acme/other", commit_hash="c2", at=_ht(72))
    write.delivery.record_delivery_repo_landed(chunk_delivery_b, repo="acme/widget", commit_hash="c3", at=_ht(73))
    write.delivery.record_delivery_landed(chunk_delivery_a, at=_ht(74))
    write.artifacts.record_hub_artifact(
        chunk_delivery_a,
        node_id=deliver_node.node_id,
        node_name="deliver",
        epoch=1,
        name=f"{MARKER_PREFIX}acme/widget",
        content="merged",
        at=_ht(75),
    )
    write.artifacts.record_hub_artifact(
        chunk_delivery_b,
        node_id=deliver_node.node_id,
        node_name="deliver",
        epoch=1,
        name=f"{MARKER_PREFIX}acme/widget",
        content="merged",
        at=_ht(76),
    )

    # --- dependencies --------------------------------------------------------------------------
    chunk_dependency_dependent = "ch_hub_dep_dependent"
    chunk_dependency_prerequisite = "ch_hub_dep_prerequisite"
    _mint(chunk_dependency_dependent, "1017", at=_ht(77))
    _mint(chunk_dependency_prerequisite, "1018", at=_ht(78))
    _mint("ch_hub_dep_dependent_2", "1019", at=_ht(79))
    _mint("ch_hub_dep_prerequisite_2", "1020", at=_ht(80))
    write.dependencies.declare(chunk_dependency_dependent, chunk_dependency_prerequisite, by="operator", at=_ht(81))
    write.dependencies.declare("ch_hub_dep_dependent_2", "ch_hub_dep_prerequisite_2", by="operator", at=_ht(82))

    # --- lifecycle (ephemeral via a real fold) ---------------------------------------------------
    chunk_lifecycle_survivor = "ch_hub_lifecycle_survivor"
    chunk_lifecycle_merged = "ch_hub_lifecycle_merged"
    _mint(chunk_lifecycle_survivor, "1021", at=_ht(83))
    _mint(chunk_lifecycle_merged, "1022", at=_ht(84))
    hub.services.group.group(chunk_lifecycle_survivor, [chunk_lifecycle_merged])

    # --- route ---------------------------------------------------------------------------------
    chunk_route_a = "ch_hub_route_a"
    chunk_route_b = "ch_hub_route_b"
    _mint(chunk_route_a, "1023", at=_ht(86))
    _mint(chunk_route_b, "1024", at=_ht(87))
    write.route.record_route(
        Route(
            chunk_id=chunk_route_a,
            runner_id=HUB_RUNNER_ID,
            workspace_id="workspace-1",
            environment_ids=["e1"],
            created_at=_ht(88),
        ),
        token_hash="route-hash-a",
        at=_ht(88),
    )
    write.route.record_lease(chunk_route_a, epoch=1, runner_id=HUB_RUNNER_ID, at=_ht(89))
    write.route.set_runner_high_water(HUB_RUNNER_ID, seq=7, at=_ht(90))
    write.route.record_route_token(chunk_route_a, token_hash="route-hash-a2", at=_ht(91))
    write.route.record_route(
        Route(
            chunk_id=chunk_route_b,
            runner_id=HUB_RUNNER_ID_2,
            workspace_id="workspace-2",
            environment_ids=["e2"],
            created_at=_ht(92),
        ),
        token_hash="route-hash-b",
        at=_ht(92),
    )

    # --- artifacts -------------------------------------------------------------------------------
    chunk_artifacts = "ch_hub_artifacts"
    _mint(chunk_artifacts, "1025", at=_ht(93))
    write.artifacts.record_hub_artifact(
        chunk_artifacts,
        node_id=deliver_node.node_id,
        node_name="deliver",
        epoch=1,
        name="asset-1",
        content="c1",
        at=_ht(94),
    )
    write.artifacts.record_hub_artifact(
        chunk_artifacts,
        node_id=deliver_node.node_id,
        node_name="deliver",
        epoch=1,
        name="asset-2",
        content="c2",
        at=_ht(95),
    )

    # --- extra work ref -----------------------------------------------------------------------
    chunk_work_refs_extra = "ch_hub_work_refs_extra"
    _mint(chunk_work_refs_extra, "1026", at=_ht(96))
    write.work_refs.add_work_refs(chunk_work_refs_extra, [WorkRef(source="secondary", ref="99")], at=_ht(97))

    # --- hub-owned work items ------------------------------------------------------------------
    work_item_1 = seed_work_item(
        work_items,
        source="hub",
        graph_id=default_graph.graph_id,
        title="w1",
        body="b1",
        author=WorkItemAuthor.user(user_1.user_id),
        stated_priority="p1",
        at=_ht(98),
    )
    work_item_2 = seed_work_item(
        work_items,
        source="hub",
        graph_id=default_graph.graph_id,
        title="w2",
        body="b2",
        author=WorkItemAuthor.user(user_1.user_id),
        stated_priority=None,
        at=_ht(99),
    )

    return HubWorld(
        hub=hub,
        engine=engine,
        clock=clock,
        store_connections=store_connections,
        write=write,
        read=read,
        auth_state=auth_state_store,
        auth_facts=auth_facts_store,
        superuser_bootstrap=superuser_bootstrap_store,
        transcript_events=transcript_events,
        garden_run=garden_run_store,
        garden_sweeps=garden_sweeps_store,
        garden_trend=garden_trend_store,
        work_items=work_items,
        default_graph=default_graph,
        graph=graph,
        retired_graph=retired_graph,
        build_node_id=build_node.node_id,
        deliver_node_id=deliver_node.node_id,
        user_1=user_1,
        user_2=user_2,
        session_id_hash=session_id_hash,
        auth_state_value=auth_state_value,
        runner_token_hash=runner_token_hash,
        routine_id=routine.routine_id,
        scope_a="blizzard",
        scope_c="legacy",
        finding_1=finding_1,
        finding_2=finding_2,
        garden_proposal_1=garden_proposal_1,
        garden_proposal_2=garden_proposal_2,
        materialized_source=materialized_source,
        materialized_ref=materialized_ref,
        work_item_ref_1=work_item_1.ref,
        work_item_ref_2=work_item_2.ref,
        run_chunk_1=run_chunk_1,
        run_chunk_1_chunk=run_chunk_1_chunk,
        chunk_ready_1=chunk_ready_1,
        chunk_ready_2=chunk_ready_2,
        chunk_transition=chunk_transition,
        chunk_migration=chunk_migration,
        chunk_decision_1=chunk_decision_1,
        chunk_decision_2=chunk_decision_2,
        question_1=question_1,
        chunk_question=chunk_question,
        chunk_artifacts=chunk_artifacts,
        chunk_delivery_a=chunk_delivery_a,
        chunk_dependency_dependent=chunk_dependency_dependent,
        chunk_dependency_prerequisite=chunk_dependency_prerequisite,
        chunk_lifecycle_merged=chunk_lifecycle_merged,
        chunk_route_a=chunk_route_a,
        chunk_route_b=chunk_route_b,
        transcript_chunk=transcript_chunk,
        transcript_segment_id=transcript_segment_id,
    )


HubRecipe = Callable[[HubWorld], object]

#: Every reflected hub ``(Protocol, method)``, mapped to a recipe against :func:`build_hub_world`'s world.
HUB_CENSUS: dict[tuple[type, str], HubRecipe] = {
    (IReadAuthStateRepository, "get"): lambda w: w.auth_state.get(w.auth_state_value),
    (IReadAuthFactsRepository, "list_recent"): lambda w: w.auth_facts.list_recent(limit=50),
    (IReadIdentityRepository, "get"): lambda w: w.hub.services.identities.get("github", "12345"),
    (IReadIdentityRepository, "list_for_user"): lambda w: w.hub.services.identities.list_for_user(w.user_1.user_id),
    (IReadIdentityRepository, "list_for_users"): lambda w: w.hub.services.identities.list_for_users(
        [w.user_1.user_id, w.user_2.user_id]
    ),
    (IReadIdentityRepository, "distinct_provider_names"): lambda w: w.hub.services.identities.distinct_provider_names(),
    (IReadSessionRepository, "get_by_hash"): lambda w: w.hub.services.sessions.get_by_hash(w.session_id_hash),
    (IReadSuperuserBootstrapRepository, "get"): lambda w: w.superuser_bootstrap.get(),
    (IReadUserRepository, "get"): lambda w: w.hub.services.users.get(w.user_1.user_id),
    (IReadUserRepository, "get_by_username"): lambda w: w.hub.services.users.get_by_username("alice"),
    (IReadUserRepository, "get_by_email"): lambda w: w.hub.services.users.get_by_email("alice@example.com"),
    (IReadUserRepository, "username_exists"): lambda w: w.hub.services.users.username_exists("alice"),
    (IReadUserRepository, "get_many"): lambda w: w.hub.services.users.get_many([w.user_1.user_id, w.user_2.user_id]),
    (IReadUserRepository, "list_all"): lambda w: w.hub.services.users.list_all(),
    (IReadTranscriptEvents, "visible_segment_ids"): lambda w: w.transcript_events.visible_segment_ids(),
    (IReadTranscriptEvents, "derived_segment_ids"): lambda w: w.transcript_events.derived_segment_ids(),
    (IReadTranscriptEvents, "candidacy"): lambda w: w.transcript_events.candidacy(
        EXTRACTOR_VERSION, chunk_id=w.transcript_chunk
    ),
    (IReadTranscriptEvents, "derivation_signature"): lambda w: w.transcript_events.derivation_signature(),
    (IReadTranscriptEvents, "segment_derivation_input"): lambda w: w.transcript_events.segment_derivation_input(
        w.transcript_segment_id
    ),
    (IReadTranscriptEvents, "derivation_marker"): lambda w: w.transcript_events.derivation_marker(
        w.transcript_segment_id, EXTRACTOR_VERSION
    ),
    (IReadTranscriptEvents, "derivation_markers"): lambda w: w.transcript_events.derivation_markers(EXTRACTOR_VERSION),
    (IReadTranscriptEvents, "segment_derivation_inputs"): lambda w: w.transcript_events.segment_derivation_inputs(
        [w.transcript_segment_id]
    ),
    (IReadTranscriptEvents, "segment_contexts"): lambda w: w.transcript_events.segment_contexts(
        [w.transcript_segment_id]
    ),
    (IReadOperationalAnalytics, "durations_by_node"): lambda w: w.hub.services.operational_analytics.durations_by_node(
        OperationalCriteria()
    ),
    (IReadOperationalAnalytics, "durations_by_graph"): lambda w: (
        w.hub.services.operational_analytics.durations_by_graph(OperationalCriteria())
    ),
    (IReadOperationalAnalytics, "spend_by_node"): lambda w: w.hub.services.operational_analytics.spend_by_node(
        OperationalCriteria()
    ),
    (IReadOperationalAnalytics, "spend_by_graph"): lambda w: w.hub.services.operational_analytics.spend_by_graph(
        OperationalCriteria()
    ),
    (IReadOperationalAnalytics, "spend_by_chunk"): lambda w: w.hub.services.operational_analytics.spend_by_chunk(
        OperationalCriteria(), limit=50
    ),
    (IReadOperationalAnalytics, "outcomes_by_node"): lambda w: w.hub.services.operational_analytics.outcomes_by_node(
        OperationalCriteria()
    ),
    (IReadAnalyticsEventQueries, "events"): lambda w: w.hub.services.analytics_events.events(
        EventQueryCriteria(extractor_version=EXTRACTOR_VERSION), limit=50
    ),
    (IReadAnalyticsEventQueries, "counts_by_file"): lambda w: w.hub.services.analytics_events.counts_by_file(
        EventQueryCriteria(extractor_version=EXTRACTOR_VERSION)
    ),
    (IReadAnalyticsEventQueries, "counts_by_skill"): lambda w: w.hub.services.analytics_events.counts_by_skill(
        EventQueryCriteria(extractor_version=EXTRACTOR_VERSION)
    ),
    (IReadAnalyticsEventQueries, "counts_by_agent_type"): lambda w: (
        w.hub.services.analytics_events.counts_by_agent_type(EventQueryCriteria(extractor_version=EXTRACTOR_VERSION))
    ),
    (IReadAnalyticsEventQueries, "counts_by_node"): lambda w: w.hub.services.analytics_events.counts_by_node(
        EventQueryCriteria(extractor_version=EXTRACTOR_VERSION)
    ),
    (IReadChunkArtifactsRepository, "load_artifacts"): lambda w: w.read.artifacts.load_artifacts(w.chunk_artifacts),
    (IReadChunkArtifactsRepository, "latest_artifact"): lambda w: w.read.artifacts.latest_artifact(
        w.chunk_artifacts, "asset-1"
    ),
    (IReadChunkArtifactsRepository, "has_hub_artifact"): lambda w: w.read.artifacts.has_hub_artifact(
        w.chunk_artifacts, node_id=w.deliver_node_id, epoch=1, name="asset-1"
    ),
    (IReadChunkDecisionsRepository, "get_decision"): lambda w: w.read.decisions.get_decision("dec_hub_1"),
    (IReadChunkDecisionsRepository, "find_decision"): lambda w: w.read.decisions.find_decision(
        w.chunk_decision_1, node_id=w.build_node_id, epoch=1
    ),
    (IReadChunkDecisionsRepository, "decision_for_chunk"): lambda w: w.read.decisions.decision_for_chunk(
        w.chunk_decision_1
    ),
    (IReadChunkDecisionsRepository, "list_open_decisions"): lambda w: w.read.decisions.list_open_decisions(),
    (IReadChunkDecisionsRepository, "dockets_for_chunks"): lambda w: w.read.decisions.dockets_for_chunks(
        [w.chunk_decision_1, w.chunk_decision_2]
    ),
    (IReadChunkDecisionsRepository, "live_decisions_for"): lambda w: w.read.decisions.live_decisions_for(
        [w.chunk_decision_1, w.chunk_decision_2]
    ),
    (IReadChunkDeliveryRepository, "landed_repos"): lambda w: w.read.delivery.landed_repos(w.chunk_delivery_a),
    (IReadChunkDeliveryRepository, "count_landed_since"): lambda w: w.read.delivery.count_landed_since(
        "acme/widget", _HUB_BASE
    ),
    (IReadChunkDeliveryRepository, "pending_close_intents"): lambda w: w.read.delivery.pending_close_intents(),
    (IReadChunkDeliveryRepository, "unmaterialized_proposals"): lambda w: w.read.delivery.unmaterialized_proposals(),
    (IReadChunkDependenciesRepository, "list_standing_edges"): lambda w: w.read.dependencies.list_standing_edges(),
    (IReadChunkDependenciesRepository, "standing_edge"): lambda w: w.read.dependencies.standing_edge(
        w.chunk_dependency_dependent, w.chunk_dependency_prerequisite
    ),
    (IReadChunkDependenciesRepository, "standing_edges_for"): lambda w: w.read.dependencies.standing_edges_for(
        w.chunk_dependency_dependent
    ),
    (IReadChunkEscalationsRepository, "list_open_escalations"): lambda w: w.read.escalations.list_open_escalations(),
    (IReadChunkEventsRepository, "list_events"): lambda w: w.read.events.list_events(),
    (IReadChunkEventsRepository, "activity_facts_since"): lambda w: w.read.events.activity_facts_since(
        _HUB_BASE, limit=50
    ),
    (IReadChunkEventsRepository, "activity_events_since"): lambda w: w.read.events.activity_events_since(
        _HUB_BASE, limit=50
    ),
    (IReadChunkFactsRepository, "load_facts"): lambda w: w.read.facts.load_facts(w.chunk_ready_1),
    (IReadChunkFactsRepository, "load_all_facts"): lambda w: w.read.facts.load_all_facts(),
    (IReadChunkFactsRepository, "load_facts_for"): lambda w: w.read.facts.load_facts_for(
        [w.chunk_ready_1, w.chunk_ready_2]
    ),
    (IReadChunkFactsRepository, "load_all_statuses"): lambda w: w.read.facts.load_all_statuses(),
    (IReadChunkFactsRepository, "status_facts_for"): lambda w: w.read.facts.status_facts_for(
        [w.chunk_ready_1, w.chunk_ready_2]
    ),
    (IReadChunkHubExecRepository, "count_live_hub_exec_slots"): lambda w: w.read.hub_exec.count_live_hub_exec_slots(),
    (IReadChunkLifecycleRepository, "is_ephemeral"): lambda w: w.read.lifecycle.is_ephemeral(w.chunk_lifecycle_merged),
    (IReadChunkMovementRepository, "accepted_transition_target"): lambda w: w.read.movement.accepted_transition_target(
        w.chunk_transition, from_node_id=w.build_node_id, epoch=2
    ),
    (IReadChunkMovementRepository, "accepted_migration"): lambda w: w.read.movement.accepted_migration(
        w.chunk_migration, from_node_id=w.build_node_id, epoch=1
    ),
    (IReadChunkQuestionsRepository, "get_question"): lambda w: w.read.questions.get_question(w.question_1),
    (IReadChunkQuestionsRepository, "list_open_questions"): lambda w: w.read.questions.list_open_questions(),
    (IReadChunkQuestionsRepository, "load_questions"): lambda w: w.read.questions.load_questions(w.chunk_question),
    (IReadChunkQueueRepository, "queue_positions"): lambda w: w.read.queue.queue_positions(),
    (IReadChunkQueueRepository, "promoted_ats"): lambda w: w.read.queue.promoted_ats(),
    (IReadChunkRecordRepository, "get"): lambda w: w.read.record.get(w.chunk_ready_1),
    (IReadChunkRecordRepository, "get_many"): lambda w: w.read.record.get_many([w.chunk_ready_1, w.chunk_ready_2]),
    (IReadChunkRecordRepository, "graph_id_of_many"): lambda w: w.read.record.graph_id_of_many(
        [w.chunk_ready_1, w.chunk_ready_2]
    ),
    (IReadChunkRecordRepository, "list_ready"): lambda w: w.read.record.list_ready(
        statuses=w.read.facts.load_all_statuses()
    ),
    (IReadChunkRecordRepository, "list_not_ready"): lambda w: w.read.record.list_not_ready(
        statuses=w.read.facts.load_all_statuses()
    ),
    (IReadChunkRecordRepository, "list_all"): lambda w: w.read.record.list_all(),
    (IReadChunkRecordRepository, "list_page"): lambda w: w.read.record.list_page(limit=50),
    (IReadChunkRouteRepository, "route_of"): lambda w: w.read.route.route_of(w.chunk_route_a),
    (IReadChunkRouteRepository, "load_all_routes"): lambda w: w.read.route.load_all_routes(),
    (IReadChunkRouteRepository, "routes_for"): lambda w: w.read.route.routes_for([w.chunk_route_a, w.chunk_route_b]),
    (IReadChunkRouteRepository, "runner_high_water"): lambda w: w.read.route.runner_high_water(HUB_RUNNER_ID),
    (IReadChunkUsageRepository, "usage_total_since"): lambda w: w.read.usage.usage_total_since(_HUB_BASE),
    (IReadChunkWorkRefsRepository, "find_live_holder"): lambda w: w.read.work_refs.find_live_holder(
        WorkRef(source="default", ref="1001")
    ),
    (IReadChunkWorkRefsRepository, "live_holders"): lambda w: w.read.work_refs.live_holders(
        [WorkRef(source="default", ref="1001"), WorkRef(source="default", ref="1002")]
    ),
    (IReadChunkWorkRefsRepository, "live_work_refs"): lambda w: w.read.work_refs.live_work_refs(),
    (IReadFindingRepository, "get"): lambda w: w.hub.services.findings.get(w.finding_1),
    (IReadFindingRepository, "get_many"): lambda w: w.hub.services.findings.get_many([w.finding_1, w.finding_2]),
    (IReadFindingRepository, "get_with_facts"): lambda w: w.hub.services.findings.get_with_facts(w.finding_1),
    (IReadFindingRepository, "list_for"): lambda w: w.hub.services.findings.list_for("gardening", "blizzard"),
    (IReadFindingRepository, "list_for_routine"): lambda w: w.hub.services.findings.list_for_routine("gardening"),
    (IReadFindingRepository, "list_across_routines"): lambda w: w.hub.services.findings.list_across_routines(),
    (IReadFindingRepository, "list_page"): lambda w: w.hub.services.findings.list_page(
        routine_name=None, scope_slug=None, limit=50
    ),
    (IReadFindingRepository, "count_by_class"): lambda w: w.hub.services.findings.count_by_class(
        "gardening", "stale-docstring"
    ),
    (IReadFindingRepository, "has_resolution_for_proposal"): lambda w: (
        w.hub.services.findings.has_resolution_for_proposal(w.garden_proposal_2)
    ),
    (IReadFindingSetRepository, "get"): lambda w: w.hub.services.finding_sets.get("fins_hub_1"),
    (IReadFindingSetRepository, "list_for_chunk"): lambda w: w.hub.services.finding_sets.list_for_chunk(w.run_chunk_1),
    (IReadFindingSetRepository, "newest_for_routine_scope"): lambda w: (
        w.hub.services.finding_sets.newest_for_routine_scope("gardening", "blizzard")
    ),
    (IReadFindingSetRepository, "newest_by_scope_for_routine"): lambda w: (
        w.hub.services.finding_sets.newest_by_scope_for_routine("gardening")
    ),
    (IReadGardenProposalClosureRepository, "get"): lambda w: w.hub.services.garden_proposal_closures.get(
        w.garden_proposal_1
    ),
    (IReadGardenProposalClosureRepository, "get_many"): lambda w: w.hub.services.garden_proposal_closures.get_many(
        [w.garden_proposal_1, w.garden_proposal_2]
    ),
    (IReadGardenProposalClosureRepository, "find_by_item"): lambda w: (
        w.hub.services.garden_proposal_closures.find_by_item(w.materialized_source, w.materialized_ref)
    ),
    (IReadGardenProposalRepository, "get"): lambda w: w.hub.services.garden_proposals.get(w.garden_proposal_1),
    (IReadGardenProposalRepository, "list_all"): lambda w: w.hub.services.garden_proposals.list_all(),
    (IReadGardenProposalRepository, "list_page"): lambda w: w.hub.services.garden_proposals.list_page(limit=50),
    (IReadGardenProposalRepository, "list_for_routine"): lambda w: w.hub.services.garden_proposals.list_for_routine(
        "gardening"
    ),
    (IReadGardenProposalRepository, "count_by_class"): lambda w: w.hub.services.garden_proposals.count_by_class(
        "gardening", "remediate"
    ),
    (IReadGardenRunRepository, "runs_in_window"): lambda w: w.garden_run.runs_in_window(
        since=_HUB_BASE, until=_HUB_UNTIL
    ),
    (IReadGardenRunRepository, "run_identity"): lambda w: w.garden_run.run_identity(w.run_chunk_1),
    (IReadGardenRunRepository, "delivered_sets"): lambda w: w.garden_run.delivered_sets(w.run_chunk_1),
    (IReadGardenSweepsRepository, "sweeps_for_routine"): lambda w: w.garden_sweeps.sweeps_for_routine("gardening"),
    (IReadGardenTrendRepository, "facts_for_trend"): lambda w: w.garden_trend.facts_for_trend(
        "gardening", since=_HUB_BASE, until=_HUB_UNTIL
    ),
    (IReadGraphRepository, "get"): lambda w: w.hub.services.graphs.get(w.graph.graph_id),
    (IReadGraphRepository, "get_enabled_by_name"): lambda w: w.hub.services.graphs.get_enabled_by_name(_HUB_GRAPH_NAME),
    (IReadGraphRepository, "list_all"): lambda w: w.hub.services.graphs.list_all(),
    (IReadGraphRepository, "any_minted"): lambda w: w.hub.services.graphs.any_minted(_HUB_GRAPH_NAME),
    (IReadGraphRepository, "newest_definition_yaml"): lambda w: w.hub.services.graphs.newest_definition_yaml(
        _HUB_GRAPH_NAME
    ),
    (IReadGraphRepository, "is_retired"): lambda w: w.hub.services.graphs.is_retired(w.retired_graph.graph_id),
    (IReadGraphRepository, "retired_graph_ids"): lambda w: w.hub.services.graphs.retired_graph_ids(),
    (IReadGraphRepository, "follow_latest"): lambda w: w.hub.services.graphs.follow_latest(w.graph.graph_id),
    (IReadGraphRepository, "load_graph_summaries"): lambda w: w.hub.services.graphs.load_graph_summaries(
        [w.graph.graph_id, w.default_graph.graph_id]
    ),
    (IReadGraphRepository, "load_node_names"): lambda w: w.hub.services.graphs.load_node_names([w.graph.graph_id]),
    (IReadGraphRepository, "list_summaries"): lambda w: w.hub.services.graphs.list_summaries(),
    (IReadGraphRepository, "graph_id_of_enabled_name"): lambda w: w.hub.services.graphs.graph_id_of_enabled_name(
        _HUB_GRAPH_NAME
    ),
    (IReadRunnerRegistry, "get_runner"): lambda w: w.hub.services.registry.get_runner(HUB_RUNNER_ID),
    (IReadRunnerRegistry, "list_runners"): lambda w: w.hub.services.registry.list_runners(),
    (IReadRunnerRegistry, "registration_for_token_hash"): lambda w: w.hub.services.registry.registration_for_token_hash(
        w.runner_token_hash
    ),
    (IReadRunnerRegistry, "list_pause_facts_since"): lambda w: w.hub.services.registry.list_pause_facts_since(
        _HUB_BASE, limit=50
    ),
    (IReadRoutineRepository, "get"): lambda w: w.hub.services.routines.get(w.routine_id),
    (IReadRoutineRepository, "get_by_name"): lambda w: w.hub.services.routines.get_by_name("gardening"),
    (IReadRoutineRepository, "list_all"): lambda w: w.hub.services.routines.list_all(),
    (IReadRoutineScopeRepository, "list_scopes"): lambda w: w.hub.services.routine_scopes.list_scopes(w.routine_id),
    (IReadRoutineScopeRepository, "list_routines"): lambda w: w.hub.services.routine_scopes.list_routines("blizzard"),
    (IReadRunContextRepository, "for_chunk"): lambda w: w.hub.services.run_context.for_chunk(w.run_chunk_1_chunk),
    (IReadScopeRepository, "get"): lambda w: w.hub.services.scopes.get(w.scope_a),
    (IReadScopeRepository, "list_all"): lambda w: w.hub.services.scopes.list_all(),
    (IReadScopeRepository, "is_retired"): lambda w: w.hub.services.scopes.is_retired(w.scope_c),
    (IReadScopeRepository, "retired_slugs"): lambda w: w.hub.services.scopes.retired_slugs(),
    (IReadTranscriptSegments, "segments_for_chunk"): lambda w: w.hub.services.transcripts.segments_for_chunk(
        w.transcript_chunk
    ),
    (IReadTranscriptSegments, "records_for_segment"): lambda w: w.hub.services.transcripts.records_for_segment(
        w.transcript_chunk, w.transcript_segment_id
    ),
    (IReadTranscriptSegments, "runner_id_for_lease"): lambda w: w.hub.services.transcripts.runner_id_for_lease(
        w.transcript_chunk, w.build_node_id, 1
    ),
    (IReadTranscriptSegments, "records_for_lease"): lambda w: w.hub.services.transcripts.records_for_lease(
        w.transcript_chunk, w.build_node_id, 1, HUB_RUNNER_ID
    ),
    (IReadWorkItemRepository, "get"): lambda w: w.work_items.get("hub", w.work_item_ref_1),
    (IReadWorkItemRepository, "list"): lambda w: w.work_items.list("hub", limit=200),
    (IReadWorkItemRepository, "get_many"): lambda w: w.work_items.get_many(
        [WorkRef(source="hub", ref=w.work_item_ref_1), WorkRef(source="hub", ref=w.work_item_ref_2)]
    ),
}

#: Hub ``IRead*`` methods with no SQL behind them at all, each reasoned below.
HUB_EXEMPTIONS: dict[tuple[type, str], str] = {
    (IReadSessionStore, "load"): (
        "blizzard.hub.cli.sessions.internal.session_file.SessionFile implements this over a local JSON "
        "file under the CLI operator's own config dir — no SQL, and not part of any hub "
        "HubServices/ChunkReadStores wiring."
    ),
}
