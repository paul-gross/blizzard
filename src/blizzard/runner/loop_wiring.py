"""Reconciliation-loop wiring (``bzh:dependency-injection``).

Every context is built over a :class:`~blizzard.runner.composition.RunnerProcess`: the
hosted driver receives the shared graph, and each standalone command builds one and
closes it after its one pass."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

import httpx

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer, NoopPlatformTracer
from blizzard.foundation.roles import collaborator
from blizzard.runner.composition import RunnerProcess, build_runner_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.internal.subprocess_worktree_git import SubprocessWorktreeGit
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.capability_snapshot import HarnessVersionCache, default_harness_id
from blizzard.runner.hub.chunk_status_cache import ReadThroughChunkViews
from blizzard.runner.hub.client import IHubClient
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from blizzard.runner.leases.internal.worker_stdout_files import WorkerStdoutFiles
from blizzard.runner.lifecycle.env_release import EnvironmentRelease
from blizzard.runner.lifecycle.judgement.internal.elicitation_files import ElicitationFiles
from blizzard.runner.lifecycle.judgement.internal.subprocess_check_runner import SubprocessCheckRunner
from blizzard.runner.lifecycle.session import HarnessSelector, SessionResolver
from blizzard.runner.lifecycle.shutdown_drain import ShutdownDrain
from blizzard.runner.loop.context import LoopConfig, LoopContext, ResolvedSubscription
from blizzard.runner.loop.retention_floor import RetentionPasses
from blizzard.runner.loop.steps import ResumeIntents
from blizzard.runner.loop.tick import tick
from blizzard.runner.process.probe import IProcessProbe
from blizzard.runner.process.worker_scratch import WorkerScratchDirs
from blizzard.runner.stores import RunnerStores
from blizzard.runner.subscriptions.internal.subscription_sampler_factory import select_sampler
from blizzard.runner.transcripts.transcript_backfill import (
    TranscriptBackfill,
    TranscriptBackfillReport,
    TranscriptReshipReport,
)
from blizzard.runner.usage.recorder import UsageRecorder

_log = get_logger("blizzard.runner.loop")

_HTTP_TIMEOUT = 30.0

_T = TypeVar("_T")


class _LazyUsageHttpClient:
    """One ``httpx.Client`` every declared subscription's sampler shares, built only when a
    sample first runs — a runner with no live subscription opens no connection pool.
    Implements :class:`~blizzard.runner.loop.context.ICloseableUsageHttpClient`
    and the zero-arg provider shape every sampler's ``http_client`` expects."""

    def __init__(self) -> None:
        self._client: httpx.Client | None = None

    def __call__(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client()
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


@collaborator
@dataclass(frozen=True)
class LoopWiring:
    """Constructs the loop's collaborators from resolved config.

    The prompts are **already-resolved** values: resolving them on the caller's own thread
    turns a missing prompt file into a startup error (``tests/test_pin_runner_loop.py``)."""

    config: RunnerConfig
    workspace_prompt: str
    runner_prompt: str
    #: The SSE broker a standalone verb hands to the graph it builds; absent when none is served.
    events: EventBroker | None = None
    #: The harness-config snapshot this process published, delivered to the graph it builds.
    bundle: BundleSnapshot | None = None

    @classmethod
    def of(
        cls, config: RunnerConfig, *, broker: EventBroker | None = None, bundle: BundleSnapshot | None = None
    ) -> LoopWiring:
        """Read the prompt files now, on the calling thread."""
        return cls(config, config.resolved_workspace_prompt(), config.resolved_runner_prompt(), broker, bundle)

    def context(
        self,
        hub: IHubClient,
        graph: RunnerProcess,
        *,
        sweep_worker_scratch: bool = False,
        tracer: IPlatformTracer | None = None,
    ) -> LoopContext:
        """Wire a :class:`LoopContext` over ``graph``, the process's one composition-root graph;
        the caller owns the ``httpx.Client`` behind ``hub``, and the returned context's own
        ``usage_http_client`` — closed the same way, once the caller is done with the context.

        ``sweep_worker_scratch`` runs the per-lease scratch directory's one-shot orphan sweep —
        ``True`` only from :class:`PeriodicDriver`'s own daemon-start build, ahead of its first
        tick, when no spawn can race it; every other caller (``tick_once`` and siblings, a build
        wired only to inspect it) leaves it off. ``tracer`` is the tick's span seam — only the daemon passes one."""
        config = self.config
        stores, provider, harnesses, clock = graph.stores, graph.provider, graph.harnesses, graph.clock
        # A startup guard: this composition's transcripts lane requires the default
        # harness's own binding to resolve one, not merely to be registered at all.
        default_id = default_harness_id(harnesses)
        if default_id is not None:
            harnesses.transcript_source(default_id)
        # Each subscription declaration paired with its resolved sampler; all share one HTTP client.
        usage_http_client = _LazyUsageHttpClient()
        resolved_subscriptions = tuple(
            ResolvedSubscription(
                slug=declaration.slug,
                name=declaration.name,
                provider=declaration.provider,
                sample_interval_seconds=declaration.sample_interval_seconds,
                sampler=select_sampler(declaration, clock=clock, http_client=usage_http_client),
            )
            for declaration in config.resolved_subscriptions()
        )
        # The per-lease harness-stdout directory, created once here so a worker's
        # stdout redirect target always exists by the time a spawn/resume opens it.
        worker_stdout_dir = config.root / "worker-stdout"
        worker_stdout_dir.mkdir(parents=True, exist_ok=True)
        # The detached elicitation's own output directory — load-bearing,
        # so it is always created, unlike `worker_stdout_dir`'s empty-disables convention.
        elicitation_output_dir = config.root / "elicitation-output"
        elicitation_output_dir.mkdir(parents=True, exist_ok=True)
        # The per-lease scratch directory (`BLIZZARD_TMPDIR`), created once here so a worker's
        # staging target always exists by the time a spawn/resume/judge opens it.
        worker_scratch_dir = config.root / "worker-tmp"
        worker_scratch_dir.mkdir(parents=True, exist_ok=True)
        _worker_scratch = WorkerScratchDirs(str(worker_scratch_dir))
        if sweep_worker_scratch:
            # A one-shot crash reconciliation — sweeping any orphan a crash left between a
            # directory's `ensure` and its owning lease's row landing.
            _worker_scratch.sweep_orphans(lease.lease_id for lease in stores.lease_record.list_active_leases())
        loop_config = LoopConfig(
            runner_name=config.name,
            workspace_id=config.workspace_id,
            max_agents=config.max_agents,
            base_branch=config.base_branch,
            env_capacity=provider.capacity(),
            public_url=config.public_url,  # this runner's own federation identity
            redirect_uris=config.redirect_uris,
            local_api_url=config.local_api_url,
            gates=config.gates,
            workspace_root=provider.spawn_root(),
            workspace_prompt=self.workspace_prompt,
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
        return LoopContext(
            stores=stores,
            clock=clock,
            hub=hub,
            identity=graph.identity,
            # The non-memoizing default — only `tick()` itself upgrades this per call.
            chunk_views=ReadThroughChunkViews(hub),
            provider=provider,
            subscriptions=resolved_subscriptions,
            usage_http_client=usage_http_client,
            process=graph.process,
            worktree_git=SubprocessWorktreeGit(),
            # The check-runner seam — see `runner/lifecycle/judgement/check_runner.py`.
            check_runner=SubprocessCheckRunner(worker_env=config.worker_env),
            config=loop_config,
            worker_files=_worker_files,
            elicitation_files=_elicitation_files,
            worker_scratch=_worker_scratch,
            usage=UsageRecorder(
                leases=stores.liveness,
                usage=stores.usage,
                clock=clock,
                worker_files=_worker_files,
                workspace_root=loop_config.workspace_root,
                harnesses=harnesses,
                invocation_boundaries=stores.invocation_boundaries,
                transcripts_wired=True,
                events=self.events,
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
                provider=provider,
                events=self.events,
            ),
            # The startup guard above already resolved the default harness's transcript
            # source (or raised) — this composition's transcripts lane is always wired.
            transcripts_wired=True,
            events=self.events,
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
            with httpx.Client(base_url=config.hub_url, timeout=_HTTP_TIMEOUT, headers=config.auth_headers()) as client:
                graph.platform_tracing.instrument_client(client)
                ctx = self.context(HttpHubClient(client), graph, tracer=graph.platform_tracing.tracer)
                try:
                    return use(ctx)
                finally:
                    ctx.usage_http_client.close()
        finally:
            if process is None:
                graph.close()

    def tick_once(self, *, process: RunnerProcess | None = None) -> None:
        """Run one synchronous reconciliation tick — the CLI verb and e2e driver.

        ``process`` runs the tick over one caller-owned graph, so a driver of repeated ticks shares the
        graph's platform tracing with the runner's local API instead of building a graph per tick."""
        self._with_context(tick, process=process)

    def backfill_transcripts(self, *, dry_run: bool, limit: int | None = None) -> TranscriptBackfillReport:
        """Run one transcript-backfill pass — the operator verb's own entry,
        wired here rather than at the CLI so the composition root stays the one place a
        context is built."""
        return self._with_context(lambda ctx: TranscriptBackfill(ctx).run(dry_run=dry_run, limit=limit))

    def reship_transcript(self, segment_id: str) -> TranscriptReshipReport:
        """Re-ship one already-imported segment — wired here for the reason above."""
        return self._with_context(lambda ctx: TranscriptBackfill(ctx).reship(segment_id))


@dataclass(frozen=True)
class ResumeMarking:
    """The ``host`` command's two restart-resume hooks (#12, #13), each over its own store.

    Store-only — no hub, no workspace provider."""

    stores: RunnerStores
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
        return ResumeIntents(self.stores).mark_crashed(process=self.process, now=self.clock.now())


class PeriodicDriver:
    """A background thread that ticks the loop on an interval (~30s).

    Owns its own ``httpx.Client`` for the driver's lifetime. A tick that raises is logged
    and swallowed so one bad pass never kills the daemon."""

    def __init__(
        self,
        config: RunnerConfig,
        *,
        interval_seconds: float,
        process_graph: RunnerProcess,
    ) -> None:
        # Wired eagerly on the constructing (``host``) thread so a missing prompt file
        # fails startup rather than the loop thread (`tests/test_runner_loop_build.py`).
        self._wiring = LoopWiring.of(config, broker=process_graph.events)
        self._graph = process_graph
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="blizzard-runner-loop", daemon=True)
        self._client: httpx.Client | None = None

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
        handshake, bounded by `DEFAULT_IDENTITY_AWAIT_TIMEOUT_SECONDS` (10s) rather than
        returning immediately — left open by design, since OpenCode self-mints its session
        id with nowhere durable to poll it from until that handshake completes."""
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        config = self._wiring.config
        self._client = httpx.Client(base_url=config.hub_url, timeout=_HTTP_TIMEOUT, headers=config.auth_headers())
        self._graph.platform_tracing.instrument_client(self._client)
        ctx: LoopContext | None = None
        try:
            ctx = self._wiring.context(
                HttpHubClient(self._client),
                self._graph,
                sweep_worker_scratch=True,
                tracer=self._graph.platform_tracing.tracer,
            )
            _log.info("reconciliation loop started", runner_name=config.name, interval=self._interval)
            while not self._stop.is_set():
                try:
                    tick(ctx)
                except Exception as exc:  # a bad tick must not kill the daemon
                    _log.error("tick failed", detail=str(exc))
                self._stop.wait(self._interval)
        finally:
            if ctx is not None:
                ctx.usage_http_client.close()
            self._client.close()
            _log.info("reconciliation loop stopped", runner_name=config.name)
