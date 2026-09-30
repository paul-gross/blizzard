"""Reconciliation-loop wiring (``bzh:dependency-injection``).

The hosted driver receives the shared process graph. Standalone commands build
their own graph and dispose it after their one pass."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.composition import RunnerProcess, build_runner_process, build_stores
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.factory import build_workspace_provider
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.health_cache import HarnessHealthCache
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.harness_registry import (
    build_production_harness_health_probes,
    build_production_harness_registry,
)
from blizzard.runner.loop.capability_snapshot import HarnessVersionCache, default_harness_id
from blizzard.runner.loop.chunk_status_cache import ReadThroughChunkViews
from blizzard.runner.loop.context import LoopConfig, LoopContext, ResolvedSubscription
from blizzard.runner.loop.elicitation_files import ElicitationFiles
from blizzard.runner.loop.env_release import EnvironmentRelease
from blizzard.runner.loop.hub import IHubClient
from blizzard.runner.loop.internal.http_hub import HttpHubClient
from blizzard.runner.loop.internal.subprocess_check_runner import SubprocessCheckRunner
from blizzard.runner.loop.internal.subprocess_worktree_git import SubprocessWorktreeGit
from blizzard.runner.loop.process import IProcessProbe, LinuxProcessProbe
from blizzard.runner.loop.retention_floor import RetentionPasses
from blizzard.runner.loop.session import HarnessSelector, SessionResolver
from blizzard.runner.loop.shutdown_drain import ShutdownDrain
from blizzard.runner.loop.steps import ResumeIntents
from blizzard.runner.loop.tick import tick
from blizzard.runner.loop.transcript_backfill import (
    TranscriptBackfill,
    TranscriptBackfillReport,
    TranscriptReshipReport,
)
from blizzard.runner.loop.usage import UsageRecorder
from blizzard.runner.loop.worker_scratch import WorkerScratchDirs
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.stores import RunnerStores
from blizzard.runner.subscriptions.internal.credential_renewer_factory import select_renewer
from blizzard.runner.subscriptions.internal.subprocess_one_shot_process import SubprocessOneShotProcess
from blizzard.runner.subscriptions.internal.subscription_sampler_factory import select_sampler

_log = get_logger("blizzard.runner.loop")

_HTTP_TIMEOUT = 30.0


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


@dataclass(frozen=True)
class LoopWiring:
    """Constructs the loop's collaborators from resolved config.

    The prompts are **already-resolved** values: resolving them on the caller's own thread
    turns a missing prompt file into a startup error (``tests/test_pin_runner_loop.py``)."""

    config: RunnerConfig
    workspace_prompt: str
    runner_prompt: str
    #: The SSE broker shared with the served app; absent on standalone commands.
    events: EventBroker | None = None
    process_graph: RunnerProcess | None = None

    @classmethod
    def of(cls, config: RunnerConfig, *, broker: EventBroker | None = None) -> LoopWiring:
        """Read the prompt files now, on the calling thread."""
        return cls(config, config.resolved_workspace_prompt(), config.resolved_runner_prompt(), broker)

    def context(
        self,
        hub: IHubClient,
        *,
        engine: Engine | None = None,
        health_cache: HarnessHealthCache | None = None,
        sweep_worker_scratch: bool = False,
    ) -> LoopContext:
        """Wire a :class:`LoopContext`; the caller owns the ``httpx.Client`` behind ``hub``,
        and the returned context's own ``usage_http_client`` — closed the same way,
        once the caller is done with the context.

        The hosted driver shares its process graph with the served app. A direct
        standalone context build can supply an engine and health cache for tests.
        ``sweep_worker_scratch`` runs the per-lease scratch directory's one-shot orphan sweep —
        ``True`` only from :class:`PeriodicDriver`'s own daemon-start build, ahead of its first
        tick, when no spawn can race it; every other caller (``tick_once`` and siblings, a build
        wired only to inspect it) leaves it off."""
        config = self.config
        graph = self.process_graph
        if graph is None:
            if engine is None:
                engine = create_engine_from_url(config.db_url)
            stores = build_stores(engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
            provider = build_workspace_provider(config, held_ids=stores.environments.held_environment_ids)
            harnesses = build_production_harness_registry(config)
        else:
            stores, provider, harnesses = graph.stores, graph.provider, graph.harnesses
        # A startup guard: this composition's transcripts lane requires the default
        # harness's own binding to resolve one, not merely to be registered at all.
        default_id = default_harness_id(harnesses)
        if default_id is not None:
            harnesses.transcript_source(default_id)
        _clock = graph.clock if graph is not None else SystemClock()
        health_cache = (graph.health if graph is not None else health_cache) or HarnessHealthCache(
            clock=_clock,
            probes=build_production_harness_health_probes(config),
            selftest_results=stores.selftest_results,
            configured_tiers={
                CLAUDE_CODE_HARNESS_ID: config.model_aliases,
                OPENCODE_HARNESS_ID: config.opencode_model_aliases,
            },
        )
        # The subscription-sampling seam — each declaration paired with its
        # resolved binding; an unknown provider selects `None` (declared, unsampled). Every
        # sampler shares one lazily-built HTTP client, owned by this context,
        # rather than opening its own.
        usage_http_client = _LazyUsageHttpClient()
        # The renewal seam's own one-shot subprocess — shared across every
        # declared subscription's renewer binding, same as the sampler's shared HTTP client.
        one_shot_subprocess = SubprocessOneShotProcess()
        resolved_subscriptions = tuple(
            ResolvedSubscription(
                slug=declaration.slug,
                name=declaration.name,
                provider=declaration.provider,
                sample_interval_seconds=declaration.sample_interval_seconds,
                sampler=select_sampler(declaration, clock=_clock, http_client=usage_http_client),
                renewer=select_renewer(declaration, clock=_clock, subprocess=one_shot_subprocess),
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
            runner_id=config.runner_id,
            workspace_id=config.workspace_id,
            max_agents=config.max_agents,
            base_branch=config.base_branch,
            env_capacity=(
                config.max_environments if config.workspace_provider == "basic" else len(config.workspace_envs)
            ),
            public_url=config.public_url,  # this runner's own federation identity
            redirect_uris=config.redirect_uris,
            local_api_url=config.local_api_url,
            gates=config.gates,
            # Basic workers run in their acquired workdir, away from shared clones.
            # Winter keeps its configured workspace-wide spawn cwd.
            workspace_root="" if config.workspace_provider == "basic" else config.workspace_root,
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
        )
        _worker_files = WorkerStdoutFiles(str(worker_stdout_dir), stores.liveness)
        _elicitation_files = ElicitationFiles(str(elicitation_output_dir))
        return LoopContext(
            stores=stores,
            clock=_clock,
            hub=hub,
            # The non-memoizing default — only `tick()` itself upgrades this per call.
            chunk_views=ReadThroughChunkViews(hub),
            provider=provider,
            subscriptions=resolved_subscriptions,
            usage_http_client=usage_http_client,
            process=graph.process if graph is not None else LinuxProcessProbe(),
            worktree_git=SubprocessWorktreeGit(),
            # The check-runner seam — see `runner/loop/checks.py`.
            check_runner=SubprocessCheckRunner(worker_env=config.worker_env),
            config=loop_config,
            worker_files=_worker_files,
            elicitation_files=_elicitation_files,
            worker_scratch=_worker_scratch,
            usage=UsageRecorder(
                leases=stores.liveness,
                usage=stores.usage,
                clock=_clock,
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
            harness_selector=HarnessSelector(harnesses=harnesses, health=health_cache),
            env_release=EnvironmentRelease(
                environments=stores.environments,
                clock=_clock,
                provider=provider,
                events=self.events,
            ),
            # The startup guard above already resolved the default harness's transcript
            # source (or raised) — this composition's transcripts lane is always wired.
            transcripts_wired=True,
            events=self.events,
            harnesses=harnesses,
            # Built once here, long-lived across every tick `PeriodicDriver._run` drives on this context.
            harness_versions=HarnessVersionCache(clock=_clock),
            # Mirrors `harness_versions`: built once, long-lived across every tick.
            harness_health=health_cache,
            # Mirrors `harness_versions`: built once, long-lived across every tick.
            retention_passes=RetentionPasses(),
        )

    def tick_once(self) -> None:
        """Run one synchronous reconciliation tick — the CLI verb and e2e driver."""
        config = self.config
        graph = build_runner_process(config, events=self.events)
        try:
            with httpx.Client(base_url=config.hub_url, timeout=_HTTP_TIMEOUT, headers=config.auth_headers()) as client:
                wiring = LoopWiring(config, self.workspace_prompt, self.runner_prompt, self.events, graph)
                ctx = wiring.context(HttpHubClient(client))
                try:
                    tick(ctx)
                finally:
                    ctx.usage_http_client.close()
        finally:
            graph.close()

    def backfill_transcripts(self, *, dry_run: bool, limit: int | None = None) -> TranscriptBackfillReport:
        """Run one transcript-backfill pass — the operator verb's own entry,
        wired here rather than at the CLI so the composition root stays the one place a
        context is built."""
        config = self.config
        graph = build_runner_process(config, events=self.events)
        try:
            with httpx.Client(base_url=config.hub_url, timeout=_HTTP_TIMEOUT, headers=config.auth_headers()) as client:
                wiring = LoopWiring(config, self.workspace_prompt, self.runner_prompt, self.events, graph)
                ctx = wiring.context(HttpHubClient(client))
                try:
                    return TranscriptBackfill(ctx).run(dry_run=dry_run, limit=limit)
                finally:
                    ctx.usage_http_client.close()
        finally:
            graph.close()

    def reship_transcript(self, segment_id: str) -> TranscriptReshipReport:
        """Re-ship one already-imported segment — wired here for the reason above."""
        config = self.config
        graph = build_runner_process(config, events=self.events)
        try:
            with httpx.Client(base_url=config.hub_url, timeout=_HTTP_TIMEOUT, headers=config.auth_headers()) as client:
                wiring = LoopWiring(config, self.workspace_prompt, self.runner_prompt, self.events, graph)
                ctx = wiring.context(HttpHubClient(client))
                try:
                    return TranscriptBackfill(ctx).reship(segment_id)
                finally:
                    ctx.usage_http_client.close()
        finally:
            graph.close()


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
        broker: EventBroker | None = None,
        harness_health: HarnessHealthCache | None = None,
        process_graph: RunnerProcess | None = None,
    ) -> None:
        # Wired eagerly on the constructing (``host``) thread so a missing prompt file
        # fails startup rather than the loop thread (`tests/test_runner_loop_build.py`).
        self._wiring = LoopWiring.of(config, broker=broker)
        if process_graph is not None:
            self._wiring = LoopWiring(
                config, self._wiring.workspace_prompt, self._wiring.runner_prompt, process_graph.events, process_graph
            )
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="blizzard-runner-loop", daemon=True)
        self._client: httpx.Client | None = None
        # Direct standalone driver construction can supply a health cache for tests.
        self._harness_health = harness_health

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
        # A standalone driver owns its engine on this thread. The hosted driver
        # uses the process graph's shared engine, disposed after recovery marking.
        engine = None if self._wiring.process_graph is not None else create_engine_from_url(config.db_url)
        ctx: LoopContext | None = None
        try:
            ctx = self._wiring.context(
                HttpHubClient(self._client),
                engine=engine,
                health_cache=self._harness_health,
                sweep_worker_scratch=True,
            )
            _log.info("reconciliation loop started", runner_id=config.runner_id, interval=self._interval)
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
            if engine is not None:
                engine.dispose()
            _log.info("reconciliation loop stopped", runner_id=config.runner_id)
