"""Test doubles for the runner loop's seams — injected at the boundaries only.

The reconciliation steps are a pure function of ``(store, clock, seam clients)``
(``bzh:steppable-loop``), so the unit tier drives them against a real (tmp sqlite)
runner store with these fakes standing in for the hub, workspace provider, harness
adapter, process probe, and worktree git. Each fake conforms to its Protocol by
type — pyright rejects drift.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import MetaData

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    IWorkspaceProvider,
    WorkspaceAcquisitionError,
)
from blizzard.runner.harness.adapter import IHarnessAdapter, WorkerHandle, WorkerPreamble
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.hub import IHubClient, RouteClaimOutcome
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.loop.worktree import GitArtifact, IWorktreeGit
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import IWriteRunnerStore
from blizzard.runner.store.schema import metadata as runner_metadata
from blizzard.wire.chunk import ChunkDetail
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeConfig, NodeEnvelope
from blizzard.wire.queue import QueuePeekEntry, QueuePeekResponse
from blizzard.wire.route import RouteClaim, RouteClaimResponse


def make_store(tmp_path_url: str) -> SqlAlchemyRunnerStore:
    """A migrated (schema-created) runner store over a fresh sqlite file."""
    engine = create_engine_from_url(tmp_path_url)
    _create_all(runner_metadata, engine)
    return SqlAlchemyRunnerStore(engine)


def _create_all(md: MetaData, engine: object) -> None:
    md.create_all(engine)  # type: ignore[arg-type]


class FakeHub:
    """A scriptable :class:`IHubClient`: canned queue/claim/apply/envelope/chunk."""

    def __init__(self) -> None:
        self.queue: list[QueuePeekEntry] = []
        self.claim_outcome: RouteClaimOutcome | None = None
        self.apply_responses: list[ApplyResponse] = []
        self.envelopes: dict[str, NodeEnvelope] = {}
        self.chunks: dict[str, ChunkDetail] = {}
        self.claims: list[RouteClaim] = []
        self.completions: list[tuple[str, CompletionSubmission]] = []

    def peek_queue(self) -> QueuePeekResponse:
        return QueuePeekResponse(entries=list(self.queue))

    def claim_route(self, claim: RouteClaim) -> RouteClaimOutcome:
        self.claims.append(claim)
        assert self.claim_outcome is not None, "no claim outcome scripted"
        return self.claim_outcome

    def submit_completion(self, chunk_id: str, submission: CompletionSubmission) -> ApplyResponse:
        self.completions.append((chunk_id, submission))
        assert self.apply_responses, "no apply response scripted"
        return self.apply_responses.pop(0)

    def get_envelope(self, chunk_id: str) -> NodeEnvelope:
        return self.envelopes[chunk_id]

    def get_chunk(self, chunk_id: str) -> ChunkDetail:
        # Default a hub-node-held chunk to `delivering` (the merge queue is still
        # working) unless a test scripts a terminal state.
        if chunk_id in self.chunks:
            return self.chunks[chunk_id]
        return ChunkDetail(
            chunk_id=chunk_id,
            graph_id="gr_1",
            status=ChunkStatus.DELIVERING,
            current_node_id="deliver",
            latest_epoch=1,
        )


class FakeProvider:
    """A scriptable :class:`IWorkspaceProvider` over a fixed pool of workdirs."""

    def __init__(self, pool: dict[str, str], *, refuse: bool = False) -> None:
        self._pool = pool  # env_id -> workdir
        self.refuse = refuse
        self.released: list[str] = []

    def acquire(self, chunk_id: str, count: int, held_ids: list[str]) -> list[AcquiredEnvironment]:
        if self.refuse:
            raise WorkspaceAcquisitionError("refused (scripted)")
        free = [(e, wd) for e, wd in self._pool.items() if e not in set(held_ids)]
        if len(free) < count:
            raise WorkspaceAcquisitionError(f"need {count}, {len(free)} free")
        return [AcquiredEnvironment(environment_id=e, workdir=wd) for e, wd in free[:count]]

    def release(self, environment_id: str) -> None:
        self.released.append(environment_id)


class FakeHarness:
    """A scriptable :class:`IHarnessAdapter`: canned spawn handle + verdict."""

    def __init__(self, *, handle: WorkerHandle, verdict: str | None) -> None:
        self._handle = handle
        self.verdict = verdict
        self.spawns: list[tuple[NodeEnvelope, WorkerPreamble]] = []
        self.judged: list[tuple[str, str, str]] = []

    def spawn(self, envelope: NodeEnvelope, preamble: WorkerPreamble, session_hint: str | None) -> WorkerHandle:
        self.spawns.append((envelope, preamble))
        return WorkerHandle(
            session_id=self._handle.session_id,
            pid=self._handle.pid,
            process_start_time=self._handle.process_start_time,
        )

    def judge(self, environment_id: str, session_id: str, judgement_prompt: str) -> str:
        self.judged.append((environment_id, session_id, judgement_prompt))
        return "<judged output>"

    def resume_with_message(self, environment_id: str, session_id: str, message: str) -> int:
        return 4321

    def resume_command(self, environment_id: str, session_id: str) -> str:
        return f"cd {environment_id} && claude --resume {session_id}"

    def parse_verdict(self, output: str) -> str | None:
        return self.verdict


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
) -> LoopContext:
    """Assemble a :class:`LoopContext` from a real store and injected fakes."""
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
        config=config if config is not None else LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1),
    )


def make_envelope(chunk_id: str, node_name: str, *, node_id: str, choices: list[tuple[str, str]]) -> NodeEnvelope:
    """A minimal runner-node envelope for a step test."""
    from blizzard.hub.domain.graph import Executor, JudgedBy, SessionMode
    from blizzard.wire.envelope import EnvelopeChoice

    node = NodeConfig(
        node_id=node_id,
        node_name=node_name,
        executor=Executor.RUNNER,
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=2,
        choices=[EnvelopeChoice(name=n, description=d) for n, d in choices],
    )
    return NodeEnvelope(
        chunk_id=chunk_id,
        graph_id="gr_test",
        epoch=1,
        node=node,
        prompt="commit('work')",
        judgement_prompt="Assess the build.",
    )


def claimed_outcome(chunk_id: str, envelope: NodeEnvelope, *, runner_id: str = "r1") -> RouteClaimOutcome:
    return RouteClaimOutcome(
        claimed=RouteClaimResponse(
            chunk_id=chunk_id,
            runner_id=runner_id,
            workspace_id="ws1",
            environment_ids=["e1"],
            envelope=envelope,
        )
    )
