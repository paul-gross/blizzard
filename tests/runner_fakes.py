"""Test doubles for the runner loop's seams — injected at the boundaries only.

The reconciliation steps are a pure function of ``(store, clock, seam clients)``
(``bzh:steppable-loop``), so the unit tier drives them against a real store with these
fakes standing in for the hub, provider, harness, probe, and worktree git.
"""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import Engine, MetaData

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock, IClock
from blizzard.foundation.node_steps import SessionMode
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    IWorkspaceProvider,
    RepoBinding,
    WorkspaceAcquisitionError,
)
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.adapter import IHarnessAdapter, WorkerHandle, WorkerPreamble
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, TranscriptBatch, TranscriptPosition
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.loop.checks import CheckOutcome, ICheckRunner
from blizzard.runner.loop.chunk_status_cache import IChunkViews, ReadThroughChunkViews
from blizzard.runner.loop.context import LoopConfig, LoopContext, ResolvedSubscription
from blizzard.runner.loop.elicitation_files import ElicitationFiles
from blizzard.runner.loop.env_release import EnvironmentRelease
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError, IHubClient, RouteClaimOutcome
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.loop.session import HarnessSelector, SessionResolver
from blizzard.runner.loop.usage import UsageRecorder
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles
from blizzard.runner.loop.worktree import IWorktreeGit
from blizzard.runner.runtime import migration_runner
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.internal.ask_store import AskStore
from blizzard.runner.store.internal.attachment_store import AttachmentStore
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.internal.check_store import CheckStore
from blizzard.runner.store.internal.elicitation_store import ElicitationStore
from blizzard.runner.store.internal.environment_store import EnvironmentStore
from blizzard.runner.store.internal.escalation_store import EscalationStore
from blizzard.runner.store.internal.git_commit_declaration_store import GitCommitDeclarationStore
from blizzard.runner.store.internal.graph_artifact_store import GraphArtifactStore
from blizzard.runner.store.internal.lease_liveness_store import LeaseLivenessStore
from blizzard.runner.store.internal.lease_record_store import LeaseRecordStore
from blizzard.runner.store.internal.lease_resume_intent_store import LeaseResumeIntentStore
from blizzard.runner.store.internal.lease_session_store import LeaseSessionStore
from blizzard.runner.store.internal.outbound_store import OutboundStore
from blizzard.runner.store.internal.pause_store import PauseStore
from blizzard.runner.store.internal.requeue_store import RequeueStore
from blizzard.runner.store.internal.takeover_store import TakeoverStore
from blizzard.runner.store.internal.token_store import TokenStore
from blizzard.runner.store.internal.transcript_ledger_store import TranscriptLedgerStore
from blizzard.runner.store.internal.usage_store import UsageStore
from blizzard.runner.store.internal.workspace_prompt_store import WorkspacePromptStore
from blizzard.runner.store.schema import metadata as runner_metadata
from blizzard.runner.store.schema import transcript_outbound_buffer, transcript_segments
from blizzard.runner.stores import (
    IReadRunnerStore,
    IWriteRunnerStore,
    RunnerReadStores,
    RunnerStores,
)
from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageSnapshot, ISubscriptionSampler
from blizzard.runner.transcripts.archived_repository import ArchivedTranscript
from blizzard.runner.transcripts.repository import IReadTranscriptRepository
from blizzard.tools.invariants import RunnerInvariants, Violation
from blizzard.wire.chunk import ChunkStatusView, HubAdvanceResponse
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import (
    ApplyOutcome,
    ApplyResponse,
    GraphArtifact,
    NodeConfig,
    NodeEnvelope,
)
from blizzard.wire.facts import RunnerFact, RunnerFactAck, RunnerFactBatch
from blizzard.wire.graph import ProducesEntry, RotatePolicyView
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekEntry, QueuePeekRequest, QueuePeekResponse
from blizzard.wire.route import RouteClaim, RouteClaimResponse, RouteTokenRekeyResponse
from blizzard.wire.runner import RunnerCapability
from blizzard.wire.transcript_segment import TranscriptSegmentAck, TranscriptSegmentBatch, TranscriptSegmentRecord


class SqlAlchemyRunnerStore(
    LeaseRecordStore,
    LeaseSessionStore,
    LeaseLivenessStore,
    LeaseResumeIntentStore,
    EnvironmentStore,
    TranscriptLedgerStore,
    TokenStore,
    WorkspacePromptStore,
    OutboundStore,
    AskStore,
    PauseStore,
    TakeoverStore,
    RequeueStore,
    EscalationStore,
    UsageStore,
    AttachmentStore,
    GitCommitDeclarationStore,
    CheckStore,
    GraphArtifactStore,
    ElicitationStore,
):
    """The flat, every-concept-at-once runner store — test support only (D3, blizzard#410):
    production composes the extracted concept adapters individually via
    :func:`~blizzard.runner.composition.build_stores`, never this class. Kept here because a
    test fixture wants one object standing in for every concept at once, structurally
    satisfying every one of the seventeen concept Protocols by inheritance."""

    def __init__(self, engine: Engine, errors: RunnerStoreErrorFactory) -> None:
        store = RunnerStoreConnections(engine, errors)
        LeaseRecordStore.__init__(self, store)
        LeaseSessionStore.__init__(self, store)
        LeaseLivenessStore.__init__(self, store)
        LeaseResumeIntentStore.__init__(self, store)
        EnvironmentStore.__init__(self, store)
        TranscriptLedgerStore.__init__(self, store)
        TokenStore.__init__(self, store)
        WorkspacePromptStore.__init__(self, store)
        OutboundStore.__init__(self, store)
        AskStore.__init__(self, store)
        PauseStore.__init__(self, store)
        TakeoverStore.__init__(self, store)
        RequeueStore.__init__(self, store)
        EscalationStore.__init__(self, store)
        UsageStore.__init__(self, store)
        AttachmentStore.__init__(self, store)
        GitCommitDeclarationStore.__init__(self, store)
        CheckStore.__init__(self, store)
        GraphArtifactStore.__init__(self, store)
        ElicitationStore.__init__(self, store)
        self._engine = engine
        self._errors = errors


def runner_store_errors() -> RunnerStoreErrorFactory:
    """The runner-store seam (issue #413) every test's ``SqlAlchemyRunnerStore``
    construction supplies — one helper so its call sites construct it identically."""
    return RunnerStoreErrorFactory(structlog.get_logger("test"))


def make_store(tmp_path_url: str) -> SqlAlchemyRunnerStore:
    """A migrated (schema-created) runner store over a fresh sqlite file."""
    engine = create_engine_from_url(tmp_path_url)
    _create_all(runner_metadata, engine)
    return SqlAlchemyRunnerStore(engine, runner_store_errors())


_runner_prototype_lock = threading.Lock()
_runner_prototype_tmp: tempfile.TemporaryDirectory[str] | None = None
_runner_prototype_db: Path | None = None


def runner_migration_prototype() -> Path:
    """A runner store migrated to head exactly once per process — a test that needs a
    real, Alembic-migrated file (rather than :func:`make_store`'s create-all shortcut)
    copies this file instead of re-running the runner's ~39 revisions.

    Safe to share regardless of the ``RunnerConfig`` a caller would otherwise build: the
    migration tree branches on nothing but its own code, never on a config field. Lives
    under its own per-process temp dir, cleaned up by :class:`tempfile.TemporaryDirectory`'s
    own finalizer."""
    global _runner_prototype_tmp, _runner_prototype_db
    with _runner_prototype_lock:
        if _runner_prototype_db is None:
            _runner_prototype_tmp = tempfile.TemporaryDirectory(prefix="blizzard-runner-migration-proto-")
            root = Path(_runner_prototype_tmp.name)
            db_url = f"sqlite:///{root / 'runner.db'}"
            migration_runner(RunnerConfig(root=root, db_url=db_url)).upgrade("head")
            _checkpoint_sqlite(db_url)
            _runner_prototype_db = root / "runner.db"
        return _runner_prototype_db


def _checkpoint_sqlite(db_url: str) -> None:
    """Flush a sqlite file's WAL back into itself and drop the connection — a bare copy
    of the ``.db`` file is only a complete store once no ``-wal``/``-shm`` sidecar is
    load-bearing."""
    engine = create_engine_from_url(db_url)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        engine.dispose()


def make_stores(store: IWriteRunnerStore) -> RunnerStores:
    """The :class:`RunnerStores` bundle over one flat store — every field the same object,
    since :class:`SqlAlchemyRunnerStore` structurally satisfies every concept Protocol."""
    return RunnerStores(
        lease_record=store,
        session=store,
        liveness=store,
        resume_intent=store,
        environments=store,
        transcript_ledger=store,
        tokens=store,
        workspace_prompt=store,
        outbound=store,
        asks=store,
        pause=store,
        takeover=store,
        requeue=store,
        escalations=store,
        usage=store,
        attachments=store,
        git_commit_declarations=store,
        checks=store,
        graph_artifacts=store,
        elicitations=store,
    )


def make_read_stores(store: IWriteRunnerStore) -> RunnerReadStores:
    """The :class:`RunnerReadStores` bundle over one flat store — :func:`make_stores`
    narrowed the way :meth:`RunnerReadStores.of` narrows a wired ``RunnerStores``."""
    return RunnerReadStores.of(make_stores(store))


def _create_all(md: MetaData, engine: object) -> None:
    md.create_all(engine)  # type: ignore[arg-type]


def strip_transcript_segments(store: object) -> None:
    """Erase the segment ledger and its lane buffer — the pre-lane store shape the
    blizzard#250 backfill exists for. Unreachable through the write API, whose only
    session-id writer stamps a segment in the same transaction.

    ``store`` is typed loosely: any one field of a :func:`make_stores` bundle is, at
    runtime, the same flat :class:`SqlAlchemyRunnerStore` this asserts for."""
    assert isinstance(store, SqlAlchemyRunnerStore)
    with store._engine.begin() as conn:
        conn.execute(transcript_outbound_buffer.delete())
        conn.execute(transcript_segments.delete())


class StubbedBufferBytesStore:
    """A real store reading a scripted ``outstanding_transcript_buffer_bytes`` — the pump's
    backpressure cap, crossed without materializing hundreds of MB of real payload. Values
    are consumed one per call and the last repeats, so a test can let one read pass the cap
    and a later one trip it. ``calls`` counts every read, for asserting ``run()``'s own
    once-per-run hoist queries this exactly once regardless of segment count."""

    def __init__(self, inner: object, *outstanding_bytes: int) -> None:
        self._inner = inner
        self._values = list(outstanding_bytes)
        self.calls = 0

    def outstanding_transcript_buffer_bytes(self) -> int:
        self.calls += 1
        return self._values.pop(0) if len(self._values) > 1 else self._values[0]

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class CountingAttachmentStore:
    """A real store, wrapped to count ``attachments_for_lease`` (full content) and
    ``attachment_names_for_lease`` (names only, Phase 3 hoist) calls separately — lets a
    test assert the produces-coverage check reads only names while `_judged`'s asset
    harvest still reads full content, on the very same lease."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.attachments_for_lease_calls: list[str] = []
        self.attachment_names_for_lease_calls: list[str] = []

    def attachments_for_lease(self, lease_id: str) -> dict[str, str]:
        self.attachments_for_lease_calls.append(lease_id)
        return self._inner.attachments_for_lease(lease_id)  # type: ignore[attr-defined,no-any-return]

    def attachment_names_for_lease(self, lease_id: str) -> set[str]:
        self.attachment_names_for_lease_calls.append(lease_id)
        return self._inner.attachment_names_for_lease(lease_id)  # type: ignore[attr-defined,no-any-return]

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def runner_invariant_violations(store: object) -> list[Violation]:
    """The runner store's durable invariants, asserted over this store's own engine — the
    checker the crash sweep runs, reachable from a component test."""
    assert isinstance(store, SqlAlchemyRunnerStore)
    return RunnerInvariants(store._engine).run()


class FakeHub:
    """A scriptable :class:`IHubClient`: canned queue/claim/apply/envelope/chunk.

    ``down`` raises :class:`HubClientError`; ``not_found`` (blizzard#9) 404s `get_envelope`
    and omits the id from a `chunk_statuses` response.
    """

    def __init__(self, *, default_runner_id: str = "r1") -> None:
        # The runner id the unscripted `chunk_statuses` fallback's route reports as holding
        # the chunk; `make_context` keeps this in sync with `LoopConfig.runner_id` (blizzard#38).
        self.default_runner_id = default_runner_id
        self.queue: list[QueuePeekEntry] = []
        # A per-call scripted sequence (blizzard#433 D10): when set, each `peek_queue`
        # call pops its own response instead of reading the static `queue` above.
        self.queue_responses: list[list[QueuePeekEntry]] = []
        self.peek_queue_calls = 0  # counts `peek_queue` calls (blizzard#459) — one per Fill.run()
        # One entry per `peek_queue` call, naming the request it carried (blizzard#433
        # Phase 3) — lets a test assert on the capabilities/policy the call site sends.
        self.peek_queue_requests: list[QueuePeekRequest] = []
        self.claim_outcome: RouteClaimOutcome | None = None
        self.apply_responses: list[ApplyResponse] = []
        self.envelopes: dict[str, NodeEnvelope] = {}
        self.chunks: dict[str, ChunkStatusView] = {}
        # One entry per `chunk_statuses` call, naming the ids it requested (blizzard#521) —
        # lets a test assert on the per-tick batching the cache is built for.
        self.chunk_statuses_calls: list[list[str]] = []
        self.claims: list[RouteClaim] = []
        self.completions: list[tuple[str, CompletionSubmission]] = []
        self.decisions_submitted: list[tuple[str, DecisionSubmission]] = []
        self.decision_responses: list[ApplyResponse] = []
        self.pushed: list[RunnerFact] = []
        self.high_water: dict[str, int] = {}
        # One entry per `push_facts` call, naming the seqs it carried — lets a test assert
        # on batching, distinct from `pushed`'s own flattened what-eventually-landed log.
        self.push_facts_calls: list[list[int]] = []
        # The transcript lane's own push log and mark — structurally separate from the
        # fact lane's above (D3, issue #246).
        self.transcripts_pushed: list[TranscriptSegmentRecord] = []
        self.transcript_high_water: dict[str, int] = {}
        # One entry per `push_transcripts` call, naming the seqs it carried (issue #522).
        self.push_transcripts_calls: list[list[int]] = []
        # Seqs to cap-reject-but-ack, scripted (review F8) — the real ingest service's own
        # size/budget/rate rejection (blizzard#247), which no fake could otherwise surface to a test.
        self.reject_transcript_seqs: set[int] = set()
        self.questions: dict[str, QuestionView] = {}
        self.delivered: list[tuple[str, QuestionView]] = []
        self.registered: list[tuple[str, str]] = []  # (runner_id, workspace_id)
        self.registered_capacities: list[int | None] = []  # env_capacity per register call (issue #69)
        self.registered_urls: list[str | None] = []  # url per register call (issue #95)
        self.registered_redirect_uris: list[tuple[str, ...]] = []  # redirect_uris per register call (issue #95)
        # capabilities per register call (blizzard#433)
        self.registered_capabilities: list[tuple[RunnerCapability, ...]] = []
        self.paused = False  # the hub-side pause brake this fake reports back
        self.down = False
        # chunk ids `get_envelope` 404s for (blizzard#9); `chunk_statuses` never raises for
        # one of these — it simply omits it from the returned mapping (blizzard#521).
        self.not_found: set[str] = set()
        self.get_envelope_calls: list[str] = []  # chunk ids `get_envelope` was called for (Phase 3 hoist)
        self.hub_advance_calls: list[str] = []  # chunk ids `hub_advance` was called for (#66)
        self.hub_advance_responses: dict[str, HubAdvanceResponse] = {}
        self.rekey_calls: list[str] = []  # chunk ids `rekey_route_token` was called for (issue #84b)
        self.rekey_responses: dict[str, str] = {}  # chunk_id -> the plaintext to hand back

    def peek_queue(self, request: QueuePeekRequest) -> QueuePeekResponse:
        self.peek_queue_calls += 1
        self.peek_queue_requests.append(request)
        if self.queue_responses:
            return QueuePeekResponse(entries=self.queue_responses.pop(0))
        return QueuePeekResponse(entries=list(self.queue))

    def claim_route(self, claim: RouteClaim) -> RouteClaimOutcome:
        self.claims.append(claim)
        assert self.claim_outcome is not None, "no claim outcome scripted"
        return self.claim_outcome

    def submit_completion(self, chunk_id: str, submission: CompletionSubmission) -> ApplyResponse:
        if self.down:
            raise HubClientError("fake hub is down")
        self.completions.append((chunk_id, submission))
        assert self.apply_responses, "no apply response scripted"
        return self.apply_responses.pop(0)

    def submit_decision(self, chunk_id: str, submission: DecisionSubmission) -> ApplyResponse:
        if self.down:
            raise HubClientError("fake hub is down")
        self.decisions_submitted.append((chunk_id, submission))
        if self.decision_responses:
            return self.decision_responses.pop(0)
        return ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail="parked at gate")

    def push_facts(self, batch: RunnerFactBatch) -> RunnerFactAck:
        if self.down:
            raise HubClientError("fake hub is down")
        self.push_facts_calls.append([fact.seq for fact in batch.facts])
        mark = self.high_water.get(batch.runner_id, 0)
        applied, already = [], []
        for fact in sorted(batch.facts, key=lambda f: f.seq):
            if fact.seq <= mark:
                already.append(fact.seq)
                continue
            self.pushed.append(fact)
            mark = fact.seq
            applied.append(fact.seq)
        self.high_water[batch.runner_id] = mark
        return RunnerFactAck(runner_id=batch.runner_id, high_water=mark, applied=applied, already_applied=already)

    def push_transcripts(self, batch: TranscriptSegmentBatch) -> TranscriptSegmentAck:
        if self.down:
            raise HubClientError("fake hub is down")
        self.push_transcripts_calls.append([record.seq for record in batch.records])
        mark = self.transcript_high_water.get(batch.runner_id, 0)
        applied, already, capped = [], [], []
        for record in sorted(batch.records, key=lambda r: r.seq):
            if record.seq <= mark:
                # Mirrors the real hub's own replay fix: a lost-ack retry of an
                # already-decided seq still reports its cap outcome, not bare idempotency.
                if record.seq in self.reject_transcript_seqs:
                    capped.append(record.seq)
                else:
                    already.append(record.seq)
                continue
            mark = record.seq
            if record.seq in self.reject_transcript_seqs:
                # Cap-rejected-but-acked: the mark still advances past it (D6/blizzard#247's
                # `TranscriptIngestService._apply` — every reachable outcome advances the mark).
                capped.append(record.seq)
                continue
            self.transcripts_pushed.append(record)
            applied.append(record.seq)
        self.transcript_high_water[batch.runner_id] = mark
        return TranscriptSegmentAck(
            runner_id=batch.runner_id, high_water=mark, applied=applied, already_applied=already, capped=capped
        )

    def get_envelope(self, chunk_id: str) -> NodeEnvelope:
        self.get_envelope_calls.append(chunk_id)
        if chunk_id in self.not_found:
            raise ChunkNotFoundError(f"chunk {chunk_id} unknown")
        return self.envelopes[chunk_id]

    def chunk_statuses(self, chunk_ids: Iterable[str]) -> dict[str, ChunkStatusView]:
        if self.down:
            raise HubClientError("fake hub is down")
        ids = list(chunk_ids)
        self.chunk_statuses_calls.append(ids)
        found: dict[str, ChunkStatusView] = {}
        for chunk_id in ids:
            if chunk_id in self.not_found:
                continue  # omitted, never a 404 — mirrors the real hub's batch-read semantics
            if chunk_id in self.chunks:
                found[chunk_id] = self.chunks[chunk_id]
                continue
            # Default a hub-node-held chunk to `delivering` with its route still ours — the
            # common case — unless a test scripts something else (e.g. a released route).
            found[chunk_id] = ChunkStatusView(
                chunk_id=chunk_id,
                status=ChunkStatus.DELIVERING,
                route_runner_id=self.default_runner_id,
                latest_epoch=1,
            )
        return found

    def hub_advance(self, chunk_id: str) -> HubAdvanceResponse:
        if self.down:
            raise HubClientError("fake hub is down")
        self.hub_advance_calls.append(chunk_id)
        if chunk_id in self.hub_advance_responses:
            return self.hub_advance_responses[chunk_id]
        return HubAdvanceResponse(
            chunk_id=chunk_id, status=ChunkStatus.DELIVERING, ran=False, detail="scripted default"
        )

    def get_question(self, question_id: str) -> QuestionView:
        return self.questions[question_id]

    def register_runner(
        self,
        runner_id: str,
        workspace_id: str,
        *,
        env_capacity: int | None = None,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        capabilities: tuple[RunnerCapability, ...] = (),
    ) -> None:
        if self.down:
            raise HubClientError("fake hub is down")
        self.registered.append((runner_id, workspace_id))
        self.registered_capacities.append(env_capacity)
        self.registered_urls.append(url)
        self.registered_redirect_uris.append(redirect_uris)
        self.registered_capabilities.append(capabilities)

    def fetch_runner_paused(self, runner_id: str) -> bool:
        if self.down:
            raise HubClientError("fake hub is down")
        return self.paused

    def rekey_route_token(self, chunk_id: str) -> RouteTokenRekeyResponse:
        if chunk_id in self.not_found:
            raise ChunkNotFoundError(f"chunk {chunk_id} unknown")
        if self.down:
            raise HubClientError("fake hub is down")
        self.rekey_calls.append(chunk_id)
        token = self.rekey_responses.get(chunk_id, "rtok_rekeyed")
        return RouteTokenRekeyResponse(chunk_id=chunk_id, route_token=token)


class FakeProvider:
    """A scriptable :class:`IWorkspaceProvider` over a fixed pool of workdirs.

    ``repos`` scripts the per-env manifest, defaulting to the stock ``toy-api`` repo.
    """

    _DEFAULT_REPOS = (("toy-api", "file:///origins/toy-api.git"),)

    def __init__(
        self,
        pool: dict[str, str],
        *,
        refuse: bool = False,
        prepare_fail: bool = False,
        repos: dict[str, Sequence[tuple[str, str]]] | None = None,
    ) -> None:
        self._pool = pool  # env_id -> workdir
        self.refuse = refuse
        self.prepare_fail = prepare_fail
        self.released: list[str] = []
        self._repos = {env: tuple(entries) for env, entries in (repos or {}).items()}

    def acquire(self, chunk_id: str, count: int, held_ids: list[str]) -> list[AcquiredEnvironment]:
        if self.refuse:
            raise WorkspaceAcquisitionError("refused (scripted)")
        if self.prepare_fail:
            raise EnvironmentPreparationError("reset step failed (scripted)", environment_id="e1", step="checkout-base")
        free = [(e, wd) for e, wd in self._pool.items() if e not in set(held_ids)]
        if len(free) < count:
            raise WorkspaceAcquisitionError(f"need {count}, {len(free)} free")
        return [AcquiredEnvironment(environment_id=e, workdir=wd) for e, wd in free[:count]]

    def release(self, environment_id: str) -> None:
        self.released.append(environment_id)

    def repos(self, environment_id: str) -> list[RepoBinding]:
        if environment_id not in self._pool:
            return []
        entries = self._repos.get(environment_id, self._DEFAULT_REPOS)
        return [
            RepoBinding(environment_id=environment_id, name=name, relpath=name, origin_url=origin)
            for name, origin in entries
        ]


class FakeTranscriptSource:
    """A scriptable :class:`IHarnessTranscriptSource`: canned batches, raw lines, sizes, and
    context sizes by session id (blizzard#245). An unscripted session reads as ``not_found``
    for turns and as *unmeasurable* for both bounds, so a test only names what it cares about.
    """

    def __init__(
        self,
        batches_by_session: dict[str, TranscriptBatch] | None = None,
        lines_by_session: dict[str, list[str]] | None = None,
        sizes_by_session: dict[str, int] | None = None,
        context_tokens_by_session: dict[str, int] | None = None,
    ) -> None:
        self._batches = batches_by_session or {}
        self._lines = lines_by_session or {}
        self._sizes = sizes_by_session or {}
        self._context_tokens = context_tokens_by_session or {}
        self.turns_since_calls: list[tuple[str, str | None, TranscriptPosition | None]] = []
        self.size_bytes_calls: list[str] = []
        self.context_tokens_calls: list[str] = []

    def turns_since(
        self, session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None
    ) -> TranscriptBatch:
        self.turns_since_calls.append((session_id, spawn_cwd, since))
        if session_id in self._batches:
            return self._batches[session_id]
        return TranscriptBatch(
            session_id=session_id,
            available=False,
            reason="not_found",
            turns=[],
            unlinked_sidechains=[],
            next_position=None,
            complete=True,
            truncated=False,
            sidechain_truncated=False,
            normalizer_version="fake/1",
            harness_version=None,
        )

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        return list(self._lines.get(session_id, []))

    def size_bytes(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        self.size_bytes_calls.append(session_id)
        return self._sizes.get(session_id)

    def context_tokens(self, session_id: str, *, spawn_cwd: str | None) -> int | None:
        self.context_tokens_calls.append(session_id)
        return self._context_tokens.get(session_id)


class FakeArchivedTranscriptRepository:
    """A scriptable :class:`IReadArchivedTranscriptRepository` (blizzard#249) — one canned
    :class:`ArchivedTranscript` per ``(chunk_id, node_id, epoch)`` key. An unscripted key
    reads as ``status="empty"``, so a test only names the leases it cares about."""

    def __init__(self, by_key: dict[tuple[str, str, int], ArchivedTranscript] | None = None) -> None:
        self._by_key = by_key or {}
        self.calls: list[tuple[str, str, int]] = []

    def read_turns(self, *, chunk_id: str, node_id: str, epoch: int) -> ArchivedTranscript:
        key = (chunk_id, node_id, epoch)
        self.calls.append(key)
        if key in self._by_key:
            return self._by_key[key]
        return ArchivedTranscript(status="empty", turns=[], truncated=False)


class StaticTranscriptRepositoryResolver:
    """A stub :class:`ITranscriptRepositoryResolver` returning the same repository regardless
    of harness id — the shape a test driving exactly one recorded owner needs."""

    def __init__(self, repository: IReadTranscriptRepository) -> None:
        self._repository = repository

    def transcript_repository(self, harness_id: str) -> IReadTranscriptRepository:
        return self._repository


class FakeHarness:
    """A scriptable :class:`IHarnessAdapter`: canned spawn handle + verdict.

    ``usage`` is the blanket reply; ``usage_by_kind`` (issue #58) overrides it per kind.
    """

    def __init__(
        self,
        *,
        handle: WorkerHandle,
        verdict: str | None,
        assessment: str = "",
        usage: UsageSample | None = None,
        usage_by_kind: dict[str, UsageSample | None] | None = None,
        transcript_usage: UsageSample | None = None,
        transcript_source: IHarnessTranscriptSource | None = None,
        judge_side_effect: Callable[[], None] | None = None,
        judge_pid: int = 8888,
        judge_pgid: int | None = None,
        judge_process_start_time: str = "judge-start",
        judge_output: str = "<judged output>",
        judge_output_usable: bool = True,
    ) -> None:
        self._handle = handle
        self.verdict = verdict
        # The detached elicitation's own (pid, start_time) (blizzard#443) — distinct from
        # `handle`'s worker pid by default, so a probe scripted around the worker's liveness
        # never accidentally also governs the elicitation's.
        self._judge_pid = judge_pid
        # Defaults to `judge_pid` (D3): a real launch's pgid always equals its own pid;
        # an explicit `judge_pgid=None` opts a test back into the "unset" shape.
        self._judge_pgid = judge_pgid if judge_pgid is not None else judge_pid
        self._judge_process_start_time = judge_process_start_time
        # What `judge` writes to its `output_path` — content is irrelevant to this fake's
        # own `parse_verdict`/`parse_usage`/`parse_assessment`, which ignore it (they read
        # `self.verdict`/`self.usage`/`self.assessment` instead), but a collect pass must
        # find a non-empty, readable file to know the launch actually landed something.
        self.judge_output = judge_output
        # `has_usable_output`'s scripted reply (blizzard#443 review, F7) — True by default so
        # every existing script's judged output reads as usable without opting in; a test
        # simulating a killed-mid-write elicitation sets this False instead of writing real
        # malformed JSON, since this fake's `parse_verdict`/`parse_usage` never inspect content.
        self.judge_output_usable = judge_output_usable
        # Fires inside `judge()`, before its reply is returned — lets a test express "the
        # worker asked instead of returning a verdict" (e.g. `store.record_ask(...)`).
        self._judge_side_effect = judge_side_effect
        self.assessment = assessment
        self.usage = usage
        self.usage_by_kind = usage_by_kind
        # The envelope-less fallback's own reply — distinct from `usage` so a test can
        # script "no envelope, but the transcript sums to this" without the two colliding.
        self.transcript_usage = transcript_usage
        self.spawns: list[tuple[NodeEnvelope, WorkerPreamble]] = []
        self.resume_froms: list[str | None] = []  # `resume_from` as seen by each spawn (issue #115)
        self.judged: list[tuple[str, str, str]] = []
        self.judge_output_paths: list[str] = []  # one entry per judge (launch) call
        self.judge_preambles: list[WorkerPreamble | None] = []  # one entry per judge call
        self.resumed: list[tuple[str, str, str]] = []  # (workdir, session_id, message)
        self.resumed_identity: list[tuple[WorkerPreamble | None, str]] = []  # (preamble, chunk_id) per resume
        self.resume_pid = 4321
        # The (model, effort) each invocation was handed (issue #144) — one entry per
        # call, for per-call-site assertions.
        self.spawn_model_effort: list[tuple[str | None, str | None]] = []
        self.judge_model_effort: list[tuple[str | None, str | None]] = []
        self.resume_efforts: list[str | None] = []
        # The compaction window each invocation was handed (blizzard#343) — one entry per
        # call, mirroring the effort lists above.
        self.spawn_compaction_windows: list[str | None] = []
        self.judge_compaction_windows: list[str | None] = []
        self.resume_compaction_windows: list[str | None] = []
        self.usage_models: list[str | None] = []
        # The (model, effort) each `resume_command` composition was handed (issue #144).
        self.resume_command_config: list[tuple[str | None, str | None]] = []
        # Scripted `resolve_model`/`resolve_effort` replies (issue #144); default echoes
        # the input verbatim for a test that doesn't care about resolution.
        self.resolved_model = "fake-model"
        # `resolve_model_strict`'s own scripted reply, independent of `resolved_model` above, so a
        # test can script "resolves nothing strictly, but still has an adapter default" for skip cases.
        self.resolved_model_strict: str | None = "fake-model"
        self.harness_version: str | None = None
        # `resolvable_tier_ids`'s scripted reply (blizzard#433); default echoes a single
        # fake tier so a capability-snapshot test sees a non-empty list without opting in.
        self.tier_ids: tuple[str, ...] = ("fake-tier",)
        self.resolved_effort: str | None = None
        self.resolved_compaction_window: str | None = None
        # Scriptable, not the null source (blizzard#245); defaults to an empty
        # `FakeTranscriptSource` (every session `not_found`, no lines, no size).
        self._transcript_source: IHarnessTranscriptSource = transcript_source or FakeTranscriptSource()

    def spawn(
        self,
        envelope: NodeEnvelope,
        preamble: WorkerPreamble,
        session_hint: str | None,
        resume_from: str | None = None,
        *,
        model: str | None = None,
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> WorkerHandle:
        self.spawns.append((envelope, preamble))
        self.resume_froms.append(resume_from)
        self.spawn_model_effort.append((model, effort))
        self.spawn_compaction_windows.append(compaction_window)
        # Mirrors the real in-place adapter contract (issue #115): a resume continues
        # under the SAME id given; a fresh spawn keeps the scripted-handle behavior.
        session_id = resume_from if resume_from is not None else self._handle.session_id
        # A `WorkerHandle` IS a `PendingWorkerHandle` (D1) — already identified, since this
        # fake, like every real binding today, knows its session id at "launch".
        return WorkerHandle(
            session_id=session_id,
            pid=self._handle.pid,
            process_start_time=self._handle.process_start_time,
            pgid=self._handle.pid,
        )

    def honors_session_hint(self) -> bool:
        return True

    def judge(
        self,
        workdir: str,
        session_id: str,
        judgement_prompt: str,
        output_path: str,
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        model: str | None = None,
        compaction_window: str | None = None,
    ) -> WorkerHandle:
        self.judged.append((workdir, session_id, judgement_prompt))
        self.judge_preambles.append(preamble)
        self.judge_model_effort.append((model, effort))
        self.judge_compaction_windows.append(compaction_window)
        self.judge_output_paths.append(output_path)
        # The side effect fires at LAUNCH, before the handle is returned — a test wanting
        # "the worker asked instead of returning a verdict" scripts it here, same as before
        # the launch/collect split (blizzard#443): the ask is recorded during the launch
        # pass, and the collect half's file readback below is what parses `verdict` off it.
        if self._judge_side_effect is not None:
            self._judge_side_effect()
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(self.judge_output)
        return WorkerHandle(
            session_id=self._handle.session_id,
            pid=self._judge_pid,
            process_start_time=self._judge_process_start_time,
            pgid=self._judge_pgid,
        )

    def resume_with_message(
        self,
        workdir: str,
        session_id: str,
        message: str,
        stdout_path: str = "",
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        compaction_window: str | None = None,
    ) -> int:
        self.resumed.append((workdir, session_id, message))
        self.resume_efforts.append(effort)
        self.resume_compaction_windows.append(compaction_window)
        # Captured separately so existing 3-tuple unpackers of `.resumed` keep working while
        # resume-identity assertions can read the preamble/chunk_id the caller supplied.
        self.resumed_identity.append((preamble, chunk_id))
        return self.resume_pid

    def resume_command(
        self,
        workdir: str,
        session_id: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        attended: bool = False,
    ) -> str:
        self.resume_command_config.append((model, effort))
        flags = "".join(f" --{name} {value}" for name, value in (("model", model), ("effort", effort)) if value)
        return f"cd {workdir} && claude --resume {session_id}{flags}"

    def identity_env(self, preamble: WorkerPreamble, chunk_id: str, session_id: str) -> dict[str, str]:
        # Mirrors the real adapter's shape (issue #258): BLIZZARD_* identity on top of an
        # allowlisted-base stand-in, plus vars a takeover must NOT forward (TERM, a secret).
        return {
            "PATH": "/daemon/venv/bin:/usr/bin",
            "HOME": "/daemon/home",
            "TERM": "daemon-term",
            "FAKE_PASSTHROUGH_SECRET": "should-never-leave-the-daemon",
            "BLIZZARD_ENV_IDS": ",".join(e.environment_id for e in preamble.environments),
            "BLIZZARD_ENV_WORKDIRS": ",".join(e.workdir for e in preamble.environments),
            "BLIZZARD_SESSION_ID": session_id,
            "BLIZZARD_CHUNK_ID": chunk_id,
            "BLIZZARD_LEASE_ID": preamble.lease_id,
            "BLIZZARD_RUNNER_URL": preamble.local_api_url,
            "BLIZZARD_LEASE_TOKEN": preamble.lease_token,
        }

    def resolve_model(self, preferences: Sequence[str]) -> str:
        return self.resolved_model

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None:
        return self.resolved_model_strict

    def resolve_effort(self, value: str | None) -> str | None:
        return self.resolved_effort if self.resolved_effort is not None else value

    def resolve_compaction_window(self, value: str | None) -> str | None:
        return self.resolved_compaction_window if self.resolved_compaction_window is not None else value

    def resolvable_tier_ids(self) -> tuple[str, ...]:
        return self.tier_ids

    def observe_version(self) -> str | None:
        return self.harness_version

    def parse_verdict(self, output: str) -> str | None:
        return self.verdict

    def has_usable_output(self, output: str) -> bool:
        return self.judge_output_usable

    def parse_assessment(self, output: str) -> str:
        return self.assessment

    def parse_usage(self, output: str, kind: UsageKind, *, model: str | None = None) -> UsageSample | None:
        self.usage_models.append(model)
        if self.usage_by_kind is not None and kind in self.usage_by_kind:
            return self.usage_by_kind[kind]
        return self.usage

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind, *, model: str | None = None) -> UsageSample:
        self.usage_models.append(model)
        return self.transcript_usage or UsageSample(
            kind=kind,
            model="fake-model",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=None,
        )

    def transcript_source(self) -> IHarnessTranscriptSource:
        return self._transcript_source


class TieredFakeHarness(FakeHarness):
    """A :class:`FakeHarness` whose ``resolve_model``/``resolve_model_strict`` do a real,
    left-to-right lookup against ``tiers`` — the second, differently-tiered adapter
    multi-harness selection needs to prove against, since today's single-adapter fleet
    cannot exercise it natively."""

    def __init__(self, *, tiers: dict[str, str], handle: WorkerHandle, default: str = "fake-model") -> None:
        super().__init__(handle=handle, verdict=None)
        self._tiers = tiers
        self.resolved_model = default
        self.tier_ids = tuple(tiers.keys())

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None:
        for entry in preferences:
            if entry in self._tiers:
                return self._tiers[entry]
        return None

    def resolve_model(self, preferences: Sequence[str]) -> str:
        return self.resolve_model_strict(preferences) or self.resolved_model


class FakeSubscriptionSampler:
    """A scriptable :class:`ISubscriptionSampler` (blizzard#436): a canned snapshot reply,
    or a scripted raise. ``sample_calls`` counts every call, for cadence asserts."""

    def __init__(
        self,
        *,
        snapshot: ExternalSubscriptionUsageSnapshot | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.raises = raises
        self.sample_calls = 0

    def sample(self) -> ExternalSubscriptionUsageSnapshot | None:
        self.sample_calls += 1
        if self.raises is not None:
            raise self.raises
        return self.snapshot


def _conforms_fake_subscription_sampler(x: FakeSubscriptionSampler) -> ISubscriptionSampler:
    return x


class FakeProbe:
    """A scriptable :class:`IProcessProbe`: an explicit set of live (pid, start)."""

    def __init__(self, alive: set[tuple[int, str]] | None = None) -> None:
        self.alive = alive if alive is not None else set()
        self.killed: list[int] = []
        self.killed_groups: list[int] = []

    def start_time(self, pid: int) -> str | None:
        for p, st in self.alive:
            if p == pid:
                return st
        return None

    def is_alive(self, pid: int, process_start_time: str) -> bool:
        return (pid, process_start_time) in self.alive

    def kill(self, pid: int) -> None:
        self.killed.append(pid)
        self.alive = {(p, st) for (p, st) in self.alive if p != pid}

    def kill_group(self, pgid: int) -> None:
        self.killed_groups.append(pgid)


class FakeWorktreeGit:
    """A scriptable :class:`IWorktreeGit`: a canned verify verdict, records every call.

    ``verified`` is a single bool or a ``{origin_url: bool}`` mapping; an absent key
    defaults ``True``."""

    def __init__(self, verified: bool | dict[str, bool] = True) -> None:
        self._verified = verified
        self.verified_calls: list[tuple[str, str, str]] = []

    def verify(self, origin_url: str, branch: str, commit: str) -> bool:
        self.verified_calls.append((origin_url, branch, commit))
        if isinstance(self._verified, dict):
            return self._verified.get(origin_url, True)
        return self._verified


class FakeCheckRunner:
    """A scriptable :class:`~blizzard.runner.loop.checks.ICheckRunner`: canned outcomes
    per command, records every call (issue #114).

    ``outcomes`` maps a command to its :class:`CheckOutcome`; unlisted returns ``default``."""

    def __init__(self, outcomes: dict[str, CheckOutcome] | None = None, *, default: CheckOutcome | None = None) -> None:
        self._outcomes = outcomes or {}
        self._default = default if default is not None else CheckOutcome(passed=True, output_tail="")
        self.calls: list[tuple[str, str, int]] = []

    def run(self, command: str, cwd: str, timeout: int) -> CheckOutcome:
        self.calls.append((command, cwd, timeout))
        return self._outcomes.get(command, self._default)


def make_context(
    store: IWriteRunnerStore,
    *,
    hub: FakeHub,
    provider: FakeProvider,
    harness: FakeHarness,
    probe: FakeProbe,
    worktree_git: FakeWorktreeGit | None = None,
    check_runner: FakeCheckRunner | None = None,
    clock: FixedClock | None = None,
    config: LoopConfig | None = None,
    events: EventBroker | None = None,
    subscriptions: tuple[ResolvedSubscription, ...] = (),
    chunk_views: IChunkViews | None = None,
) -> LoopContext:
    """Assemble a :class:`LoopContext` from a real store and injected fakes.

    ``chunk_views`` defaults to a fresh :class:`ReadThroughChunkViews` over ``hub`` — a step
    driven directly (not through ``tick()``) reads the hub on every ``get()``, exactly as
    ``get_chunk`` did before the per-tick cache (blizzard#521)."""
    resolved_config = config if config is not None else LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1)
    # Derived, not duplicated (blizzard#38): keeps the fake's unscripted `chunk_statuses`
    # route matching this context's actual runner_id.
    hub.default_runner_id = resolved_config.runner_id
    _hub: IHubClient = hub
    _chunk_views: IChunkViews = chunk_views if chunk_views is not None else ReadThroughChunkViews(hub=_hub)
    _provider: IWorkspaceProvider = provider
    _harnesses = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=harness.transcript_source())}
    )
    _transcripts_wired = harness.transcript_source() is not None
    _probe: IProcessProbe = probe
    _wt: IWorktreeGit = worktree_git if worktree_git is not None else FakeWorktreeGit()
    _check_runner: ICheckRunner = check_runner if check_runner is not None else FakeCheckRunner()
    _clock: IClock = clock if clock is not None else FixedClock(datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC))
    _files = WorkerStdoutFiles(resolved_config.worker_stdout_dir, store)
    # Load-bearing (D4) — unlike `worker_stdout_dir`, never the empty-disables convention,
    # so an unset config falls back to a fresh throwaway directory rather than "" (which
    # would make every `FakeHarness.judge` write raise `IsADirectoryError`/`FileNotFoundError`).
    _elicitation_root = resolved_config.elicitation_output_dir or tempfile.mkdtemp(prefix="blizzard-elicit-")
    _elicitation_files = ElicitationFiles(_elicitation_root)
    _stores = make_stores(store)
    return LoopContext(
        stores=_stores,
        clock=_clock,
        hub=_hub,
        chunk_views=_chunk_views,
        provider=_provider,
        process=_probe,
        subscriptions=subscriptions,
        worktree_git=_wt,
        check_runner=_check_runner,
        config=resolved_config,
        worker_files=_files,
        elicitation_files=_elicitation_files,
        usage=UsageRecorder(
            leases=store,
            usage=store,
            clock=_clock,
            worker_files=_files,
            workspace_root=resolved_config.workspace_root,
            harnesses=_harnesses,
            transcripts_wired=_transcripts_wired,
            events=events,
        ),
        sessions=SessionResolver(
            leases=store,
            harnesses=_harnesses,
            transcripts_wired=_transcripts_wired,
        ),
        harness_selector=HarnessSelector(harnesses=_harnesses),
        env_release=EnvironmentRelease(
            environments=store, leases=store, clock=_clock, provider=_provider, worker_files=_files, events=events
        ),
        # Mirrors `LoopWiring.context`'s own composition: wired exactly when `harness`
        # itself holds a transcript source, resolved once here.
        transcripts_wired=_transcripts_wired,
        events=events,
        harnesses=_harnesses,
    )


def _default_harness_registry(harness: IHarnessAdapter | None) -> HarnessRegistry:
    """The single-``claude_code``-binding registry both helpers below default to — a fresh
    :class:`FakeHarness` unless the caller names its own."""
    _harness = (
        harness
        if harness is not None
        else FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict=None)
    )
    return HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=_harness, transcript_source=_harness.transcript_source())}
    )


def make_usage_recorder(
    store: IWriteRunnerStore, clock: IClock, *, harness: IHarnessAdapter | None = None
) -> UsageRecorder:
    """A recorder for a context assembled without :func:`make_context`, recording nowhere useful."""
    return UsageRecorder(
        leases=store,
        usage=store,
        clock=clock,
        worker_files=WorkerStdoutFiles("", store),
        workspace_root="",
        harnesses=_default_harness_registry(harness),
    )


def make_session_resolver(store: IReadRunnerStore, *, harness: IHarnessAdapter | None = None) -> SessionResolver:
    """A resolver for a context assembled without :func:`make_context`."""
    return SessionResolver(
        leases=store,
        harnesses=_default_harness_registry(harness),
    )


def make_envelope(
    chunk_id: str,
    node_name: str,
    *,
    node_id: str,
    choices: list[tuple[str, str]],
    produces: list[str | ProducesEntry] | None = None,
    epoch: int = 0,
    session: SessionMode | None = None,
    session_source: str | None = None,
    session_name: str | None = None,
    session_model: list[str] | None = None,
    session_harnesses: list[str] | None = None,
    session_effort: str | None = None,
    session_compaction_window: str | None = None,
    session_rotate: RotatePolicyView | None = None,
    checks: list[str] | None = None,
    checks_cwd: str | None = None,
    checks_timeout: int | None = None,
    requires_checks: set[str] | None = None,
    graph_artifacts: list[GraphArtifact] | None = None,
    retries_max: int | None = 2,
) -> NodeEnvelope:
    """A minimal runner-node envelope for a step test.

    ``epoch`` defaults to 0 (never-leased); ``session`` defaults ``FRESH``; ``produces`` is a bare name
    (``kind=asset``) or explicit :class:`~blizzard.wire.graph.ProducesEntry`; ``graph_artifacts`` defaults empty;
    ``retries_max`` defaults to 2, with ``None`` modeling an omitted `retries:`."""
    from blizzard.foundation.node_steps import Executor, JudgedBy
    from blizzard.wire.envelope import EnvelopeChoice

    gated = requires_checks or set()
    node = NodeConfig(
        node_id=node_id,
        node_name=node_name,
        executor=Executor.RUNNER,
        session=session if session is not None else SessionMode.FRESH,
        session_source=session_source,
        session_name=session_name,
        session_model=session_model or [],
        session_harnesses=session_harnesses or [],
        session_effort=session_effort,
        session_compaction_window=session_compaction_window,
        session_rotate=session_rotate,
        judged_by=JudgedBy.WORKER,
        retries_max=retries_max,
        checks=checks or [],
        checks_cwd=checks_cwd,
        checks_timeout=checks_timeout,
        produces=[p if isinstance(p, ProducesEntry) else ProducesEntry(name=p) for p in produces or []],
        choices=[EnvelopeChoice(name=n, description=d, requires_checks=n in gated) for n, d in choices],
    )
    return NodeEnvelope(
        chunk_id=chunk_id,
        graph_id="gr_test",
        epoch=epoch,
        node=node,
        prompt="commit('work')",
        judgement_prompt="Assess the build.",
        graph_artifacts=graph_artifacts or [],
    )


def claimed_outcome(
    chunk_id: str, envelope: NodeEnvelope, *, runner_id: str = "r1", route_token: str = "rtok_test"
) -> RouteClaimOutcome:
    return RouteClaimOutcome(
        claimed=RouteClaimResponse(
            chunk_id=chunk_id,
            runner_id=runner_id,
            workspace_id="ws1",
            environment_ids=["e1"],
            envelope=envelope,
            route_token=route_token,
        )
    )


def no_retry_delay(seconds: float) -> None:
    """A ``HubProxy`` retry-delay double that never sleeps — every component test wiring a
    hub double that can answer with a retryable failure injects this via ``create_app``'s
    ``hub_retry_delay`` instead of riding the real ``time.sleep`` default."""
