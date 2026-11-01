"""Reconciliation-loop wiring (``bzh:dependency-injection``).

Every context is built over a :class:`~blizzard.runner.composition.RunnerProcess`: the
hosted driver receives the shared graph, and each standalone command builds one and
closes it after its one pass."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from typing import Protocol, TypeVar

import httpx2

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer, NoopPlatformTracer
from blizzard.foundation.roles import collaborator
from blizzard.runner.composition import HostProcess, RunnerProcess, build_runner_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.internal.git import SubprocessEnvGit
from blizzard.runner.environments.internal.subprocess_worktree_git import WORKTREE_GIT_TIMEOUT, SubprocessWorktreeGit
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.capability_snapshot import HarnessVersionCache, default_harness_id
from blizzard.runner.host_layout import (
    ELICITATION_OUTPUT_DIRNAME,
    WORKER_SCRATCH_DIRNAME,
    WORKER_STDOUT_DIRNAME,
)
from blizzard.runner.hub.chunk_status_cache import ReadThroughChunkViews
from blizzard.runner.hub.client import BindingDeclaration, IHubClient, ITokenIdentityReader
from blizzard.runner.hub.internal.http_hub import HeldTokenAuth, HttpHubClient
from blizzard.runner.hub.verified import VerifiedHubClient
from blizzard.runner.leases.internal.worker_stdout_files import WorkerStdoutFiles
from blizzard.runner.lifecycle.env_release import EnvironmentRelease
from blizzard.runner.lifecycle.identity_check import IdentityCheck
from blizzard.runner.lifecycle.judgement.internal.elicitation_files import ElicitationFiles
from blizzard.runner.lifecycle.judgement.internal.subprocess_check_runner import SubprocessCheckRunner
from blizzard.runner.lifecycle.session import HarnessSelector, SessionResolver
from blizzard.runner.lifecycle.shutdown_drain import ShutdownDrain
from blizzard.runner.loop.context import LoopConfig, LoopContext, ResolvedSubscription
from blizzard.runner.loop.retention_floor import RetentionPasses
from blizzard.runner.loop.step_passes import HostPassStepper, PassResult, StepLock, TickInProgress, ended
from blizzard.runner.loop.steps import ResumeIntents
from blizzard.runner.loop.tick import tick
from blizzard.runner.process.internal.worker_scratch_dirs import WorkerScratchDirs
from blizzard.runner.process.probe import IProcessProbe
from blizzard.runner.stores import HostStores, RunnerStores
from blizzard.runner.transcripts.transcript_backfill import (
    TranscriptBackfill,
    TranscriptBackfillReport,
    TranscriptReshipReport,
)
from blizzard.runner.usage.recorder import UsageRecorder

_log = get_logger("blizzard.runner.loop")

_HTTP_TIMEOUT = 30.0

_T = TypeVar("_T")


@collaborator
@dataclass(frozen=True)
class LoopWiring:
    """Constructs the loop's collaborators from resolved config.

    The runner prompt is an **already-resolved** value: resolving it on the caller's own thread
    turns a missing prompt file into a startup error (``tests/test_pin_runner_loop.py``); each workspace's prompt
    is resolved where the host builds its workspaces."""

    config: RunnerConfig
    runner_prompt: str
    #: The SSE broker a standalone verb hands to the graph it builds; absent when none is served.
    events: EventBroker | None = None
    #: The harness-config snapshot this process published, delivered to the graph it builds.
    bundle: BundleSnapshot | None = None

    @classmethod
    def of(
        cls, config: RunnerConfig, *, broker: EventBroker | None = None, bundle: BundleSnapshot | None = None
    ) -> LoopWiring:
        """Read the runner prompt file now, on the calling thread."""
        return cls(config, config.resolved_runner_prompt(), broker, bundle)

    def context(
        self,
        hub: IVerifiableHubClient,
        graph: RunnerProcess,
        *,
        sweep_worker_scratch: bool = False,
        tracer: IPlatformTracer | None = None,
    ) -> LoopContext:
        """Wire a :class:`LoopContext` over ``graph``, the process's one composition-root graph;
        the caller owns the ``httpx2.Client`` behind ``hub``.

        ``sweep_worker_scratch`` runs the per-lease scratch directory's one-shot orphan sweep — safe
        only when no spawn can race it, so off by default. ``tracer`` is the tick's span seam, absent by default."""
        config = self.config
        stores, workspaces, harnesses, clock = graph.stores, graph.workspaces, graph.harnesses, graph.clock
        # A startup guard: this composition's transcripts lane requires the default
        # harness's own binding to resolve one, not merely to be registered at all.
        default_id = default_harness_id(harnesses)
        if default_id is not None:
            harnesses.transcript_source(default_id)
        # Each subscription declaration the runner reads; the host samples them.
        resolved_subscriptions = tuple(
            ResolvedSubscription(
                slug=declaration.slug,
                name=declaration.name,
                provider=declaration.provider,
                sample_interval_seconds=declaration.sample_interval_seconds,
            )
            for declaration in config.resolved_subscriptions()
        )
        # The per-lease harness-stdout directory, created once here so a worker's
        # stdout redirect target always exists by the time a spawn/resume opens it.
        worker_stdout_dir = config.root / WORKER_STDOUT_DIRNAME
        worker_stdout_dir.mkdir(parents=True, exist_ok=True)
        # The detached elicitation's own output directory — load-bearing,
        # so it is always created, unlike `worker_stdout_dir`'s empty-disables convention.
        elicitation_output_dir = config.root / ELICITATION_OUTPUT_DIRNAME
        elicitation_output_dir.mkdir(parents=True, exist_ok=True)
        # The per-lease scratch directory (`BLIZZARD_TMPDIR`), created once here so a worker's
        # staging target always exists by the time a spawn/resume/judge opens it.
        # Keyed by runner id: a host serves several runners from one root, and each sweeps only its own subtree.
        worker_scratch_root = config.root / WORKER_SCRATCH_DIRNAME
        worker_scratch_dir = worker_scratch_root / graph.runner_id
        worker_scratch_dir.mkdir(parents=True, exist_ok=True)
        _worker_scratch = WorkerScratchDirs(str(worker_scratch_dir))
        if sweep_worker_scratch:
            # A one-shot crash reconciliation — sweeping any orphan a crash left between a
            # directory's `ensure` and its owning lease's row landing.
            active = {lease.lease_id for lease in stores.lease_record.list_active_leases()}
            _worker_scratch.sweep_orphans(active)
            # Pre-upgrade entries sit directly under the shared root, unkeyed by runner. One is this runner's to
            # remove only when this runner's own store holds its lease and that lease has closed.
            WorkerScratchDirs(str(worker_scratch_root)).sweep_flat_entries(
                lambda name: stores.lease_record.lease(name) is not None and name not in active,
            )
        # The tool profile the runner's own `git` and check commands launch under: no lease identity.
        tool_env = config.tool_env(graph.trace_settings)
        loop_config = LoopConfig(
            runner_name=config.name,
            runner_id=graph.runner_id,
            public_url=config.public_url,  # this runner's own federation identity
            redirect_uris=config.redirect_uris,
            local_api_url=replace(config, runner_id=graph.runner_id).local_api_url,
            gates=config.gates,
            projects=config.projects,
            bindings=(
                None
                if config.bindings is None
                else tuple(
                    BindingDeclaration(b.project, b.workspace, config.binding_agents(b)) for b in config.bindings
                )
            ),
            agents=config.agents,
            subscription=resolved_subscriptions[0].slug if resolved_subscriptions else None,
            declares_subscriptions=not graph.host.registered(),
            throttle=config.throttle,
            host_zone=graph.host.zone,
            runner_prompt=self.runner_prompt,
            worker_stdout_dir=str(worker_stdout_dir),
            worker_stdout_retention_days=config.worker_stdout_retention_days,
            elicitation_output_dir=str(elicitation_output_dir),
            chunk_cap_usd=config.chunk_cap_usd,
            runner_ceiling_usd=config.runner_ceiling_usd,
            runner_ceiling_window_hours=config.runner_ceiling_window_hours,
            context_warn_tokens=config.context_warn_tokens,
            context_sample_interval_seconds=config.context_sample_interval_seconds,
            runner_dir=str(config.root),
            transcripts_ship=config.transcripts_ship,
            transcript_record_max_bytes=config.transcript_record_max_bytes,
            transcript_chunk_max_bytes=config.transcript_chunk_max_bytes,
            queue_strict=config.queue_strict,
            platform_tracing=graph.platform_tracing.enabled,
            worker_program_tracing=graph.platform_tracing.enabled and config.tracing.worker_programs,
        )
        _worker_files = WorkerStdoutFiles(str(worker_stdout_dir), stores.liveness)
        _elicitation_files = ElicitationFiles(str(elicitation_output_dir))
        # Every hub call but the identity read waits on the process's verdict.
        verified = VerifiedHubClient(hub, graph.identity_verdict)
        return LoopContext(
            stores=stores,
            host_stores=graph.host.host_stores,
            clock=clock,
            hub=verified,
            identity=graph.identity,
            token_identity=hub,
            identity_verdict=graph.identity_verdict,
            hub_token=graph.hub_token,
            flags=graph.flags,
            hub_version=graph.hub_version,
            # The non-memoizing default — only `tick()` itself upgrades this per call.
            chunk_views=ReadThroughChunkViews(verified),
            workspaces=workspaces,
            subscriptions=resolved_subscriptions,
            process=graph.process,
            worktree_git=SubprocessWorktreeGit(
                SubprocessEnvGit(children=graph.children, env=tool_env, timeout=WORKTREE_GIT_TIMEOUT)
            ),
            # The check-runner seam — see `src/blizzard/runner/lifecycle/judgement/check_runner.py`.
            check_runner=SubprocessCheckRunner(children=graph.children, env=tool_env),
            config=loop_config,
            worker_files=_worker_files,
            elicitation_files=_elicitation_files,
            worker_scratch=_worker_scratch,
            usage=UsageRecorder(
                leases=stores.liveness,
                usage=stores.usage,
                clock=clock,
                worker_files=_worker_files,
                workspaces=workspaces,
                harnesses=harnesses,
                invocation_boundaries=stores.invocation_boundaries,
                transcripts_wired=True,
                events=graph.events,
            ),
            sessions=SessionResolver(
                leases=stores.session,
                harnesses=harnesses,
                transcripts_wired=True,
            ),
            harness_selector=HarnessSelector(harnesses=harnesses, health=graph.health),
            env_release=EnvironmentRelease(
                environments=stores.environments,
                clock=clock,
                workspaces=workspaces,
                events=graph.events,
            ),
            # The startup guard above already resolved the default harness's transcript
            # source (or raised) — this composition's transcripts lane is always wired.
            transcripts_wired=True,
            events=graph.events,
            harnesses=harnesses,
            # Built once here, long-lived across every tick `PeriodicDriver._run` drives on this context.
            harness_versions=HarnessVersionCache(clock=clock),
            # Mirrors `harness_versions`: built once, long-lived across every tick.
            harness_health=graph.health,
            # Mirrors `harness_versions`: built once, long-lived across every tick.
            retention_passes=RetentionPasses(),
            tracer=tracer or NoopPlatformTracer(),
        )

    def _with_context(self, use: Callable[[LoopContext], _T], *, process: RunnerProcess | None = None) -> _T:
        """Build the process graph, the hub client and one context over them, run ``use``,
        and close all three — the standalone verbs' one shared lifecycle.

        A caller-supplied ``process`` is used as it is, with its platform tracing on the client and the
        tick, and is left open for the caller to close."""
        config = self.config
        graph = process or build_runner_process(config, events=self.events, bundle=self.bundle)
        try:
            with httpx2.Client(
                base_url=config.hub_url, timeout=_HTTP_TIMEOUT, auth=HeldTokenAuth(graph.hub_token)
            ) as client:
                graph.platform_tracing.instrument_client(client)
                ctx = self.context(HttpHubClient(client), graph, tracer=graph.tracer)
                return use(ctx)
        finally:
            if process is None:
                graph.close()

    def tick_once(self, *, process: RunnerProcess | None = None) -> None:
        """Run one synchronous reconciliation tick.

        ``process`` runs the tick over a caller-owned graph, which this call neither builds nor closes;
        without one, the tick builds and closes its own."""
        self._with_context(tick, process=process)

    def backfill_transcripts(self, *, dry_run: bool, limit: int | None = None) -> TranscriptBackfillReport:
        """Run one transcript-backfill pass — the operator verb's own entry,
        wired here rather than at the CLI so the composition root stays the one place a
        context is built."""
        return self._with_context(
            lambda ctx: TranscriptBackfill(_identity_checked(ctx)).run(dry_run=dry_run, limit=limit)
        )

    def reship_transcript(self, segment_id: str) -> TranscriptReshipReport:
        """Re-ship one already-imported segment — wired here for the reason above."""
        return self._with_context(lambda ctx: TranscriptBackfill(_identity_checked(ctx)).reship(segment_id))


def _identity_checked(ctx: LoopContext) -> LoopContext:
    """``ctx`` after the identity check an operator verb's own process owes before its first other hub call —
    the tick's head, run once: until it matches, the context's hub answers every call as an outage."""
    IdentityCheck(ctx).run()
    return ctx


class IVerifiableHubClient(IHubClient, ITokenIdentityReader, Protocol):
    """A hub client that also answers the identity read the tick's identity check asks."""


@dataclass(frozen=True)
class ResumeMarking:
    """The ``host`` command's two restart-resume hooks (#12, #13), each over its own store.

    Store-only — no hub, no workspace provider."""

    stores: RunnerStores
    host_stores: HostStores
    clock: IClock
    process: IProcessProbe
    #: The drain's injected wait (``bzh:injected-clock``) — a test steps `clock` from here.
    sleep: Callable[[float], None] = time.sleep

    def on_shutdown(self) -> int:
        """Mark in-flight leases as the daemon exits gracefully, then drain their workers:
        SIGINT each marked lease's process group, wait out the shared deadline, SIGKILL any
        survivor. An ungraceful ``kill -9`` never reaches this path, which is the intended
        scope boundary."""
        marked = ResumeIntents(self.stores).mark_graceful(now=self.clock.now())
        if marked:
            self._drain()
        return marked

    def _drain(self) -> None:
        marked_ids = self.stores.resume_intent.resume_intent_lease_ids()
        leases = [lease for lease in self.stores.lease_record.list_active_leases() if lease.lease_id in marked_ids]
        ShutdownDrain(process=self.process, clock=self.clock, sleep=self.sleep).run(leases)

    def on_startup(self) -> int:
        """Mark the sessions a crash orphaned, before the loop starts — the ungraceful
        counterpart, needing a process probe as well as the store."""
        return ResumeIntents(self.stores).mark_crashed(
            process=self.process, liveness=self.host_stores.daemon_liveness, now=self.clock.now()
        )


class RunnerStepper:
    def __init__(self, config: RunnerConfig, *, process_graph: RunnerProcess) -> None:
        self._wiring = LoopWiring.of(config, broker=process_graph.host.events)
        self._graph = process_graph
        self._tick_lock = StepLock()
        self._trace_lock = StepLock()
        self._build = threading.Lock()
        self._client: httpx2.Client | None = None
        self._ctx: LoopContext | None = None

    @property
    def wiring(self) -> LoopWiring:
        return self._wiring

    def traces(self) -> bool:
        return self._graph.trace_sweep is not None

    def request(self) -> PassResult:
        with ExitStack() as held:
            held.enter_context(self._tick_lock.hold())
            if self.traces():
                held.enter_context(self._trace_lock.hold())
            clock = self._graph.clock
            started_at = clock.now()
            faults: list[Exception] = []
            for run in (self._tick, self._sweep_traces):
                try:
                    run()
                except Exception as exc:
                    _log.error("tick failed" if run == self._tick else "lease trace sweep failed", detail=str(exc))
                    faults.append(exc)
            joined = RuntimeError("; ".join(str(f) or type(f).__name__ for f in faults)) if faults else None
            return ended(clock, started_at, joined)

    def timed_tick(self) -> None:
        try:
            with self._tick_lock.hold():
                self._tick()
        except TickInProgress:
            _log.info("tick skipped; another pass is in progress", runner_name=self._wiring.config.name)
        except Exception as exc:  # a bad tick must not kill the daemon
            _log.error("tick failed", detail=str(exc))

    def timed_trace_sweep(self) -> None:
        try:
            with self._trace_lock.hold():
                self._sweep_traces()
        except TickInProgress:
            _log.info("lease trace sweep skipped; another pass is in progress")
        except Exception as exc:
            get_logger("blizzard.runner.trace_export").error("lease trace sweep failed", detail=str(exc))

    def quiesce(self) -> None:
        self._tick_lock.wait_idle()

    def close(self) -> None:
        with self._build:
            if self._client is not None:
                self._client.close()
                self._client = None
            self._ctx = None

    def _tick(self) -> None:
        tick(self._context())

    def _sweep_traces(self) -> None:
        if self._graph.trace_sweep is not None:
            self._graph.trace_sweep.sweep()

    def _context(self) -> LoopContext:
        with self._build:
            if self._ctx is None:
                config = self._wiring.config
                client = httpx2.Client(
                    base_url=config.hub_url, timeout=_HTTP_TIMEOUT, auth=HeldTokenAuth(self._graph.hub_token)
                )
                self._graph.platform_tracing.instrument_client(client)
                try:
                    self._ctx = self._wiring.context(
                        HttpHubClient(client), self._graph, sweep_worker_scratch=True, tracer=self._graph.tracer
                    )
                except BaseException:
                    client.close()
                    raise
                self._client = client
            return self._ctx


def host_pass_stepper(host: HostProcess) -> HostPassStepper:
    return HostPassStepper(
        host.host_tick.run, clock=host.clock, log=get_logger("blizzard.runner.subscriptions"), label="host tick"
    )


class PeriodicDriver:
    """A background thread that ticks the loop on an interval (~30s) through a ``RunnerStepper``."""

    def __init__(
        self,
        config: RunnerConfig,
        *,
        interval_seconds: float,
        process_graph: RunnerProcess,
        stepper: RunnerStepper | None = None,
    ) -> None:
        # Wired eagerly on the constructing (``host``) thread so a missing prompt file
        # fails startup rather than the loop thread (`tests/test_runner_loop_build.py`).
        self._owned = stepper is None
        self._stepper = stepper or RunnerStepper(config, process_graph=process_graph)
        self._wiring = self._stepper.wiring
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="blizzard-runner-loop", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Signal the loop to stop and wait for any in-flight tick to finish before returning.

        The join is **unbounded** on purpose: the graceful-shutdown resume marking runs
        right after this returns and must not race a live tick writing the same store. A
        tick cannot run forever — every seam it touches is timeout-bounded, including the
        judgement elicitation itself: `judge` launches detached and returns
        immediately rather than blocking a tick on a live model turn. One known exception:
        a fresh OpenCode spawn still blocks the tick synchronously on its own identity
        handshake, bounded by `DEFAULT_IDENTITY_AWAIT_TIMEOUT_SECONDS` (60s) rather than
        returning immediately — left open by design, since OpenCode self-mints its session
        id with nowhere durable to poll it from until that handshake completes."""
        self._stop.set()
        self._thread.join()
        if self._owned:
            self._stepper.close()

    def _run(self) -> None:
        name = self._wiring.config.name
        _log.info("reconciliation loop started", runner_name=name, interval=self._interval)
        while not self._stop.is_set():
            self._stepper.timed_tick()
            self._stop.wait(self._interval)
        _log.info("reconciliation loop stopped", runner_name=name)
