"""Test doubles for the runner loop's seams — injected at the boundaries only.

The reconciliation steps are a pure function of ``(store, clock, seam clients)``
(``bzh:steppable-loop``), so the unit tier drives them against a real (tmp sqlite)
runner store with these fakes standing in for the hub, workspace provider, harness
adapter, process probe, and worktree git. Each fake conforms to its Protocol by
type — pyright rejects drift.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import MetaData

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.graph import SessionMode
from blizzard.hub.domain.work import DEFAULT_MODEL, ChunkStatus
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    IWorkspaceProvider,
    WorkspaceAcquisitionError,
)
from blizzard.runner.harness.adapter import IHarnessAdapter, WorkerHandle, WorkerPreamble
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError, IHubClient, RouteClaimOutcome
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.loop.worktree import GitArtifact, IWorktreeGit
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import IWriteRunnerStore
from blizzard.runner.store.schema import metadata as runner_metadata
from blizzard.runner.transcripts.repository import Transcript
from blizzard.wire.chunk import ChunkDetail, HubAdvanceResponse, RouteView
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeConfig, NodeEnvelope
from blizzard.wire.facts import RunnerFact, RunnerFactAck, RunnerFactBatch
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekEntry, QueuePeekResponse
from blizzard.wire.route import RouteClaim, RouteClaimResponse, RouteTokenRekeyResponse


def make_store(tmp_path_url: str) -> SqlAlchemyRunnerStore:
    """A migrated (schema-created) runner store over a fresh sqlite file."""
    engine = create_engine_from_url(tmp_path_url)
    _create_all(runner_metadata, engine)
    return SqlAlchemyRunnerStore(engine)


def _create_all(md: MetaData, engine: object) -> None:
    md.create_all(engine)  # type: ignore[arg-type]


class FakeHub:
    """A scriptable :class:`IHubClient`: canned queue/claim/apply/envelope/chunk.

    ``down`` simulates an unreachable hub — ``submit_completion`` and ``push_facts``
    raise :class:`HubClientError` so store-and-forward buffering can be exercised.
    ``push_facts`` keeps a per-runner high-water mark and re-acks a replayed
    seq without re-applying, mirroring the hub's idempotency contract. ``not_found``
    simulates a chunk the hub no longer knows about (blizzard#9) — ``get_chunk`` and
    ``get_envelope`` raise :class:`ChunkNotFoundError` for any chunk id it names,
    checked before ``down`` since a 404 is a distinguishable outcome, not a transport
    failure.
    """

    def __init__(self, *, default_runner_id: str = "r1") -> None:
        # The runner id the unscripted `get_chunk` fallback's route reports as holding the
        # chunk. `make_context` sets this to match whatever `LoopConfig.runner_id` the context
        # is actually wired to (default or explicit), so "the route is ours" stays true by
        # construction rather than by two literals happening to agree (blizzard#38).
        self.default_runner_id = default_runner_id
        self.queue: list[QueuePeekEntry] = []
        self.claim_outcome: RouteClaimOutcome | None = None
        self.apply_responses: list[ApplyResponse] = []
        self.envelopes: dict[str, NodeEnvelope] = {}
        self.chunks: dict[str, ChunkDetail] = {}
        self.claims: list[RouteClaim] = []
        self.completions: list[tuple[str, CompletionSubmission]] = []
        self.decisions_submitted: list[tuple[str, DecisionSubmission]] = []
        self.decision_responses: list[ApplyResponse] = []
        self.leases: list[tuple[str, int, str]] = []  # (chunk_id, epoch, runner_id)
        self.escalations: list[tuple[str, int, str, str]] = []  # (chunk_id, epoch, runner_id, takeover)
        self.pushed: list[RunnerFact] = []
        self.high_water: dict[str, int] = {}
        self.questions: dict[str, QuestionView] = {}
        self.delivered: list[tuple[str, QuestionView]] = []
        self.registered: list[tuple[str, str]] = []  # (runner_id, workspace_id)
        self.registered_capacities: list[int | None] = []  # env_capacity per register call (issue #69)
        self.paused = False  # the hub-side pause brake this fake reports back
        self.down = False
        self.not_found: set[str] = set()  # chunk ids `get_chunk`/`get_envelope` 404 for (blizzard#9)
        self.hub_advance_calls: list[str] = []  # chunk ids `hub_advance` was called for (#66)
        self.hub_advance_responses: dict[str, HubAdvanceResponse] = {}
        self.rekey_calls: list[str] = []  # chunk ids `rekey_route_token` was called for (issue #84b)
        self.rekey_responses: dict[str, str] = {}  # chunk_id -> the plaintext to hand back

    def peek_queue(self) -> QueuePeekResponse:
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

    def get_envelope(self, chunk_id: str) -> NodeEnvelope:
        if chunk_id in self.not_found:
            raise ChunkNotFoundError(f"chunk {chunk_id} unknown")
        return self.envelopes[chunk_id]

    def get_chunk(self, chunk_id: str) -> ChunkDetail:
        if chunk_id in self.not_found:
            raise ChunkNotFoundError(f"chunk {chunk_id} unknown")
        if self.down:
            raise HubClientError("fake hub is down")
        # Default a hub-node-held chunk to `delivering` (the merge queue is still
        # working) with its route still ours — the common case, since a test that
        # seeds a lease this fake never claimed still owns a live route in reality
        # (nothing has told the hub otherwise) — unless a test scripts something else,
        # e.g. a released/reassigned route.
        if chunk_id in self.chunks:
            return self.chunks[chunk_id]
        return ChunkDetail(
            chunk_id=chunk_id,
            graph_id="gr_1",
            status=ChunkStatus.DELIVERING,
            current_node_id="deliver",
            latest_epoch=1,
            model=DEFAULT_MODEL,
            route=RouteView(runner_id=self.default_runner_id, workspace_id="ws1", environment_ids=[]),
        )

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

    def register_runner(self, runner_id: str, workspace_id: str, *, env_capacity: int | None = None) -> None:
        if self.down:
            raise HubClientError("fake hub is down")
        self.registered.append((runner_id, workspace_id))
        self.registered_capacities.append(env_capacity)

    def fetch_runner_paused(self, runner_id: str) -> bool:
        if self.down:
            raise HubClientError("fake hub is down")
        return self.paused

    def report_lease(self, chunk_id: str, *, epoch: int, runner_id: str) -> None:
        self.leases.append((chunk_id, epoch, runner_id))

    def report_escalation(self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str) -> None:
        self.escalations.append((chunk_id, epoch, runner_id, takeover_command))

    def rekey_route_token(self, chunk_id: str) -> RouteTokenRekeyResponse:
        if chunk_id in self.not_found:
            raise ChunkNotFoundError(f"chunk {chunk_id} unknown")
        if self.down:
            raise HubClientError("fake hub is down")
        self.rekey_calls.append(chunk_id)
        token = self.rekey_responses.get(chunk_id, "rtok_rekeyed")
        return RouteTokenRekeyResponse(chunk_id=chunk_id, route_token=token)


class FakeProvider:
    """A scriptable :class:`IWorkspaceProvider` over a fixed pool of workdirs."""

    def __init__(self, pool: dict[str, str], *, refuse: bool = False, prepare_fail: bool = False) -> None:
        self._pool = pool  # env_id -> workdir
        self.refuse = refuse
        self.prepare_fail = prepare_fail
        self.released: list[str] = []

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


class FakeHarness:
    """A scriptable :class:`IHarnessAdapter`: canned spawn handle + verdict.

    ``usage`` is the blanket ``parse_usage``/``sum_transcript_usage`` reply used by
    most tests (one sample regardless of ``kind`` or which stdout/transcript is read);
    ``usage_by_kind`` (issue #58) overrides it per :class:`UsageKind` for a test that
    needs to tell the spawn/resume fact from the judge fact apart, or to force one
    ``kind`` envelope-less (``None`` in the map) while another still parses — a
    per-kind entry set to ``None`` explicitly returns no envelope for that kind rather
    than falling back to ``usage``.
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
    ) -> None:
        self._handle = handle
        self.verdict = verdict
        self.assessment = assessment
        self.usage = usage
        self.usage_by_kind = usage_by_kind
        # The envelope-less fallback's own reply — distinct from `usage` so a test can
        # script "no envelope, but the transcript sums to this" without the two colliding.
        self.transcript_usage = transcript_usage
        self.spawns: list[tuple[NodeEnvelope, WorkerPreamble]] = []
        self.resume_froms: list[str | None] = []  # `resume_from` as seen by each spawn (issue #115)
        self.judged: list[tuple[str, str, str]] = []
        self.resumed: list[tuple[str, str, str]] = []  # (workdir, session_id, message)
        self.resumed_identity: list[tuple[WorkerPreamble | None, str]] = []  # (preamble, chunk_id) per resume
        self.resume_pid = 4321

    def spawn(
        self,
        envelope: NodeEnvelope,
        preamble: WorkerPreamble,
        session_hint: str | None,
        resume_from: str | None = None,
    ) -> WorkerHandle:
        self.spawns.append((envelope, preamble))
        self.resume_froms.append(resume_from)
        # Mirrors the real in-place adapter contract (issue #115, plan Q1): a resume
        # continues under the SAME id it was given, never the scripted handle's; a
        # fresh spawn (`resume_from is None`) keeps today's scripted-handle behavior.
        session_id = resume_from if resume_from is not None else self._handle.session_id
        return WorkerHandle(
            session_id=session_id,
            pid=self._handle.pid,
            process_start_time=self._handle.process_start_time,
        )

    def judge(self, workdir: str, session_id: str, judgement_prompt: str) -> str:
        self.judged.append((workdir, session_id, judgement_prompt))
        return "<judged output>"

    def resume_with_message(
        self,
        workdir: str,
        session_id: str,
        message: str,
        stdout_path: str = "",
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
    ) -> int:
        self.resumed.append((workdir, session_id, message))
        # Captured separately so existing 3-tuple unpackers of `.resumed` keep working while
        # resume-identity assertions can read the preamble/chunk_id the caller supplied.
        self.resumed_identity.append((preamble, chunk_id))
        return self.resume_pid

    def resume_command(self, workdir: str, session_id: str) -> str:
        return f"cd {workdir} && claude --resume {session_id}"

    def parse_verdict(self, output: str) -> str | None:
        return self.verdict

    def parse_assessment(self, output: str) -> str:
        return self.assessment

    def parse_usage(self, output: str, kind: UsageKind) -> UsageSample | None:
        if self.usage_by_kind is not None and kind in self.usage_by_kind:
            return self.usage_by_kind[kind]
        return self.usage

    def sum_transcript_usage(self, lines: Sequence[str], kind: UsageKind) -> UsageSample:
        return self.transcript_usage or UsageSample(
            kind=kind,
            model="fake-model",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=None,
        )


class FakeTranscripts:
    """A scriptable :class:`IReadTranscriptRepository`: canned raw lines by session id
    (issue #58's envelope-less usage fallback). ``read_turns`` is unused by the loop —
    stubbed only to satisfy the Protocol."""

    def __init__(self, lines_by_session: dict[str, list[str]] | None = None) -> None:
        self._lines = lines_by_session or {}

    def read_turns(self, session_id: str, *, spawn_cwd: str | None) -> Transcript:
        return Transcript(session_id=session_id, available=False, reason="not_found", turns=[], truncated=False)

    def read_raw_lines(self, session_id: str, *, spawn_cwd: str | None) -> list[str]:
        return list(self._lines.get(session_id, []))


class FakeProbe:
    """A scriptable :class:`IProcessProbe`: an explicit set of live (pid, start)."""

    def __init__(self, alive: set[tuple[int, str]] | None = None) -> None:
        self.alive = alive if alive is not None else set()
        self.killed: list[int] = []

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


class FakeWorktreeGit:
    """A scriptable :class:`IWorktreeGit`: canned produced artifacts, records pushes."""

    def __init__(self, artifacts: list[GitArtifact] | None = None) -> None:
        self._artifacts = artifacts if artifacts is not None else []
        self.pushed: list[tuple[str, str]] = []

    def find_produced_artifacts(self, env_workdir: str, base_branch: str) -> list[GitArtifact]:
        return list(self._artifacts)

    def push(self, repo_workdir: str, branch_name: str) -> None:
        self.pushed.append((repo_workdir, branch_name))


def make_context(
    store: IWriteRunnerStore,
    *,
    hub: FakeHub,
    provider: FakeProvider,
    harness: FakeHarness,
    probe: FakeProbe,
    worktree_git: FakeWorktreeGit | None = None,
    clock: FixedClock | None = None,
    config: LoopConfig | None = None,
    transcripts: FakeTranscripts | None = None,
) -> LoopContext:
    """Assemble a :class:`LoopContext` from a real store and injected fakes."""
    resolved_config = config if config is not None else LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1)
    # Derived, not duplicated (blizzard#38): the fake's unscripted `get_chunk` route always
    # reports the runner this context is actually for, so a test that passes a custom
    # `LoopConfig(runner_id=...)` can never have every lease silently read as reassigned.
    hub.default_runner_id = resolved_config.runner_id
    _hub: IHubClient = hub
    _provider: IWorkspaceProvider = provider
    _harness: IHarnessAdapter = harness
    _probe: IProcessProbe = probe
    _wt: IWorktreeGit = worktree_git if worktree_git is not None else FakeWorktreeGit()
    return LoopContext(
        store=store,
        clock=clock if clock is not None else FixedClock(datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)),
        hub=_hub,
        provider=_provider,
        harness=_harness,
        process=_probe,
        worktree_git=_wt,
        config=resolved_config,
        transcripts=transcripts,
    )


def make_envelope(
    chunk_id: str,
    node_name: str,
    *,
    node_id: str,
    choices: list[tuple[str, str]],
    produces: list[str] | None = None,
    epoch: int = 0,
    session: SessionMode | None = None,
    session_source: str | None = None,
) -> NodeEnvelope:
    """A minimal runner-node envelope for a step test.

    ``epoch`` is the hub-supplied epoch floor the claim response carries — the runner's
    mint seeds off ``max(local, envelope.epoch)`` since #112. It defaults to 0, the value
    the hub sends for a fresh, never-leased claim (``latest_epoch(facts) or 0``); a test
    modelling a reclaim of a chunk with prior hub history (e.g. a migration) passes the
    carried-forward floor explicitly.

    ``session``/``session_source`` (issue #115) default to ``SessionMode.FRESH``/``None``
    — today's unchanged behavior — unless a resume-mode test overrides them."""
    from blizzard.hub.domain.graph import Executor, JudgedBy
    from blizzard.wire.envelope import EnvelopeChoice

    node = NodeConfig(
        node_id=node_id,
        node_name=node_name,
        executor=Executor.RUNNER,
        session=session if session is not None else SessionMode.FRESH,
        session_source=session_source,
        judged_by=JudgedBy.WORKER,
        retries_max=2,
        produces=produces or [],
        choices=[EnvelopeChoice(name=n, description=d) for n, d in choices],
    )
    return NodeEnvelope(
        chunk_id=chunk_id,
        graph_id="gr_test",
        epoch=epoch,
        node=node,
        prompt="commit('work')",
        judgement_prompt="Assess the build.",
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
