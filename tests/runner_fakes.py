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
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    IWorkspaceProvider,
    RepoBinding,
    WorkspaceAcquisitionError,
)
from blizzard.runner.harness.adapter import IHarnessAdapter, WorkerHandle, WorkerPreamble
from blizzard.runner.harness.external_usage import ExternalSubscriptionUsageSnapshot
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, TranscriptBatch, TranscriptPosition
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.loop.checks import CheckOutcome, ICheckRunner
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError, IHubClient, RouteClaimOutcome
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.loop.worktree import IWorktreeGit
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import IWriteRunnerStore
from blizzard.runner.store.schema import metadata as runner_metadata
from blizzard.wire.chunk import ChunkDetail, HubAdvanceResponse, RouteView
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeConfig, NodeEnvelope, RotatePolicyView
from blizzard.wire.facts import RunnerFact, RunnerFactAck, RunnerFactBatch
from blizzard.wire.graph import ProducesEntry
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
        self.escalations: list[tuple[str, int, str, str]] = []
        # (chunk_id, epoch, runner_id, takeover) — `report_escalation` also accepts
        # `wrapped_takeover_command` (protocol parity with the real hub client), but
        # nothing in `src/` calls this dedicated route (escalation reporting rides the
        # buffered `push_facts` path instead) and no test reads a wider tuple here, so
        # it stays untracked rather than widening this bookkeeping shape for nothing.
        self.pushed: list[RunnerFact] = []
        self.high_water: dict[str, int] = {}
        self.questions: dict[str, QuestionView] = {}
        self.delivered: list[tuple[str, QuestionView]] = []
        self.registered: list[tuple[str, str]] = []  # (runner_id, workspace_id)
        self.registered_capacities: list[int | None] = []  # env_capacity per register call (issue #69)
        self.registered_urls: list[str | None] = []  # url per register call (issue #95)
        self.registered_redirect_uris: list[tuple[str, ...]] = []  # redirect_uris per register call (issue #95)
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

    def register_runner(
        self,
        runner_id: str,
        workspace_id: str,
        *,
        env_capacity: int | None = None,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
    ) -> None:
        if self.down:
            raise HubClientError("fake hub is down")
        self.registered.append((runner_id, workspace_id))
        self.registered_capacities.append(env_capacity)
        self.registered_urls.append(url)
        self.registered_redirect_uris.append(redirect_uris)

    def fetch_runner_paused(self, runner_id: str) -> bool:
        if self.down:
            raise HubClientError("fake hub is down")
        return self.paused

    def report_lease(self, chunk_id: str, *, epoch: int, runner_id: str) -> None:
        self.leases.append((chunk_id, epoch, runner_id))

    def report_escalation(
        self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str, wrapped_takeover_command: str = ""
    ) -> None:
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
    """A scriptable :class:`IWorkspaceProvider` over a fixed pool of workdirs.

    ``repos`` scripts the per-env manifest as ``{env_id: [(name, origin_url), ...]}``.
    It defaults to the suite's stock ``toy-api`` repo for every pooled env, since almost
    every test that reaches a git-commit declaration declares exactly that one; a test
    exercising several repos, a differing origin, or an empty (nothing-authorized)
    manifest names its own.
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
    """A scriptable :class:`IHarnessTranscriptSource`: canned batches, raw lines, and
    sizes by session id (blizzard#245) — the one fake every consumer of the harness's
    transcript seam scripts against, panel and loop alike (``FakeHarness.transcript_source``,
    ``harness.transcript_source()``). An unscripted session reads as ``not_found`` — today's
    null-source shape — so a test only names the sessions it cares about.
    """

    def __init__(
        self,
        batches_by_session: dict[str, TranscriptBatch] | None = None,
        lines_by_session: dict[str, list[str]] | None = None,
        sizes_by_session: dict[str, int] | None = None,
    ) -> None:
        self._batches = batches_by_session or {}
        self._lines = lines_by_session or {}
        self._sizes = sizes_by_session or {}
        self.turns_since_calls: list[tuple[str, str | None, TranscriptPosition | None]] = []

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
        return self._sizes.get(session_id)


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
        external_usage_snapshot: ExternalSubscriptionUsageSnapshot | None = None,
        external_usage_raises: Exception | None = None,
        transcript_source: IHarnessTranscriptSource | None = None,
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
        self.judge_preambles: list[WorkerPreamble | None] = []  # one entry per judge call
        self.resumed: list[tuple[str, str, str]] = []  # (workdir, session_id, message)
        self.resumed_identity: list[tuple[WorkerPreamble | None, str]] = []  # (preamble, chunk_id) per resume
        self.resume_pid = 4321
        # The (model, effort) each invocation was handed (issue #144) — one entry per
        # call, so a test can assert the application contract per call site rather than
        # only per node-entry path.
        self.spawn_model_effort: list[tuple[str | None, str | None]] = []
        self.judge_model_effort: list[tuple[str | None, str | None]] = []
        self.resume_efforts: list[str | None] = []
        self.usage_models: list[str | None] = []
        # The (model, effort) each `resume_command` composition was handed (issue #144).
        self.resume_command_config: list[tuple[str | None, str | None]] = []
        # Scripted `resolve_model`/`resolve_effort` replies (issue #144). The default
        # echoes the first preference / the value verbatim, so a test that does not care
        # about resolution sees what it passed in.
        self.resolved_model = "fake-model"
        self.resolved_effort: str | None = None
        # The scripted `sample_external_subscription_usage` reply (issue #218, phase 2):
        # `external_usage_snapshot` is returned verbatim; `external_usage_raises`, when
        # set, is raised instead, though the real adapter contract promises never to
        # raise. `external_usage_calls` counts every invocation so a test can assert the
        # cadence gate skipped (or did not skip) sampling.
        self.external_usage_snapshot = external_usage_snapshot
        self.external_usage_raises = external_usage_raises
        self.external_usage_calls = 0
        # Scriptable, not the null source (blizzard#245) — a test that drives the panel
        # projection or the loop's usage-fallback/rotation reads needs canned batches,
        # lines, or sizes. Defaults to an empty `FakeTranscriptSource` (every session
        # `not_found`, no lines, no size).
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
    ) -> WorkerHandle:
        self.spawns.append((envelope, preamble))
        self.resume_froms.append(resume_from)
        self.spawn_model_effort.append((model, effort))
        # Mirrors the real in-place adapter contract (issue #115, plan Q1): a resume
        # continues under the SAME id it was given, never the scripted handle's; a
        # fresh spawn (`resume_from is None`) keeps today's scripted-handle behavior.
        session_id = resume_from if resume_from is not None else self._handle.session_id
        return WorkerHandle(
            session_id=session_id,
            pid=self._handle.pid,
            process_start_time=self._handle.process_start_time,
        )

    def judge(
        self,
        workdir: str,
        session_id: str,
        judgement_prompt: str,
        *,
        preamble: WorkerPreamble | None = None,
        chunk_id: str = "",
        effort: str | None = None,
        model: str | None = None,
    ) -> str:
        self.judged.append((workdir, session_id, judgement_prompt))
        self.judge_preambles.append(preamble)
        self.judge_model_effort.append((model, effort))
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
        effort: str | None = None,
    ) -> int:
        self.resumed.append((workdir, session_id, message))
        self.resume_efforts.append(effort)
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
        # Mirrors the real adapter's shape (issue #258): the BLIZZARD_* identity ON TOP
        # of an allowlisted-base stand-in — fixed daemon-side values for PATH/HOME plus
        # vars a takeover must NOT forward (the daemon's TERM, an env_passthrough
        # secret), so a consumer that fails to bound what it takes is caught by a test.
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

    def resolve_effort(self, value: str | None) -> str | None:
        return self.resolved_effort if self.resolved_effort is not None else value

    def parse_verdict(self, output: str) -> str | None:
        return self.verdict

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

    def sample_external_subscription_usage(self) -> ExternalSubscriptionUsageSnapshot | None:
        self.external_usage_calls += 1
        if self.external_usage_raises is not None:
            raise self.external_usage_raises
        return self.external_usage_snapshot

    def transcript_source(self) -> IHarnessTranscriptSource:
        return self._transcript_source


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
    """A scriptable :class:`IWorktreeGit`: a canned verify verdict, records every call.

    ``verified`` is either a single bool applied to every call, or a
    ``{origin_url: bool}`` mapping for a test that needs some declarations to verify
    and others not to — an absent key defaults ``True`` (the common case: every
    declaration verifies)."""

    def __init__(self, verified: bool | dict[str, bool] = True) -> None:
        self._verified = verified
        self.verified_calls: list[tuple[str, str, str]] = []

    def verify(self, origin_url: str, branch: str, commit: str) -> bool:
        self.verified_calls.append((origin_url, branch, commit))
        if isinstance(self._verified, dict):
            return self._verified.get(origin_url, True)
        return self._verified


class FakeCheckRunner:
    """A scriptable :class:`~blizzard.runner.loop.checks.ICheckRunner`: a canned outcome
    per command (or a default), records every call (issue #114).

    ``outcomes`` maps a check command to its :class:`CheckOutcome`; an unlisted command
    returns ``default`` (a green, empty-tail pass unless overridden) — so the common
    "every check green" case needs no scripting, and a red-check test names just the one
    it wants failing."""

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
    _check_runner: ICheckRunner = check_runner if check_runner is not None else FakeCheckRunner()
    return LoopContext(
        store=store,
        clock=clock if clock is not None else FixedClock(datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)),
        hub=_hub,
        provider=_provider,
        harness=_harness,
        process=_probe,
        worktree_git=_wt,
        check_runner=_check_runner,
        config=resolved_config,
        # Mirrors `build_loop_context`'s own composition: the same source `harness`
        # itself holds, resolved once here rather than reached through `ctx.harness`.
        transcripts=harness.transcript_source(),
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
    session_effort: str | None = None,
    session_rotate: RotatePolicyView | None = None,
    checks: list[str] | None = None,
    checks_cwd: str | None = None,
    checks_timeout: int | None = None,
    requires_checks: set[str] | None = None,
) -> NodeEnvelope:
    """A minimal runner-node envelope for a step test.

    ``epoch`` is the hub-supplied epoch floor the claim response carries — the runner's
    mint seeds off ``max(local, envelope.epoch)`` since #112. It defaults to 0, the value
    the hub sends for a fresh, never-leased claim (``latest_epoch(facts) or 0``); a test
    modelling a reclaim of a chunk with prior hub history (e.g. a migration) passes the
    carried-forward floor explicitly.

    ``session``/``session_source`` (issue #115) default to ``SessionMode.FRESH``/``None``
    — today's unchanged behavior — unless a resume-mode test overrides them.
    ``session_name``/``session_model``/``session_effort``/``session_rotate`` (issue #144)
    are the hub-resolved effective declaration; all absent is a node belonging to no pool
    and expressing no preference, which is every pre-#144 envelope.

    ``produces`` entries are a bare name (``kind=asset``, the pre-#143 shape every
    existing caller passes) or an explicit :class:`~blizzard.wire.graph.ProducesEntry`
    (e.g. carrying ``kind=git_commit``) for a test that needs a kind-carrying spec."""
    from blizzard.hub.domain.graph import Executor, JudgedBy
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
        session_effort=session_effort,
        session_rotate=session_rotate,
        judged_by=JudgedBy.WORKER,
        retries_max=2,
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
