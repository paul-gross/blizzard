"""Composition root for the reconciliation loop (``bzh:dependency-injection``).

The single place the loop's collaborators are constructed from resolved config and
injected into a :class:`LoopContext`. Both :meth:`LoopWiring.tick_once` and
:class:`PeriodicDriver` open the seam clients here and close them on exit."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import httpx
from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.composition import build_stores
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.harness_registry import (
    build_production_harness_health_probes,
    build_production_harness_registry,
)
from blizzard.runner.loop.capability_snapshot import HarnessHealthCache, HarnessVersionCache
from blizzard.runner.loop.chunk_status_cache import ReadThroughChunkViews
from blizzard.runner.loop.context import LoopConfig, LoopContext, ResolvedSubscription
from blizzard.runner.loop.elicitation_files import ElicitationFiles
from blizzard.runner.loop.env_release import EnvironmentRelease
from blizzard.runner.loop.hub import IHubClient
from blizzard.runner.loop.internal.http_hub import HttpHubClient
from blizzard.runner.loop.internal.subprocess_check_runner import SubprocessCheckRunner
from blizzard.runner.loop.internal.subprocess_worktree_git import SubprocessWorktreeGit
from blizzard.runner.loop.process import IProcessProbe, LinuxProcessProbe
from blizzard.runner.loop.session import HarnessSelector, SessionResolver
from blizzard.runner.loop.steps import ResumeIntents
from blizzard.runner.loop.tick import tick
from blizzard.runner.loop.transcript_backfill import (
    TranscriptBackfill,
    TranscriptBackfillReport,
    TranscriptReshipReport,
)
from blizzard.runner.loop.usage import UsageRecorder
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.stores import RunnerStores
from blizzard.runner.subscriptions.internal.subscription_sampler_factory import select_sampler

_log = get_logger("blizzard.runner.loop")

_HTTP_TIMEOUT = 30.0


class _LazyUsageHttpClient:
    """One ``httpx.Client`` every declared subscription's sampler shares, built only when a
    sample first runs (blizzard#436, hub:95) — a runner with no live subscription opens no
    connection pool. Implements :class:`~blizzard.runner.loop.context.ICloseableUsageHttpClient`
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
    #: The SSE broker (D2, blizzard#317) shared with the served app when one composer
    #: builds both; ``None`` for a loop-only caller (``blizzard runner tick`` and siblings).
    events: EventBroker | None = None

    @classmethod
    def of(cls, config: RunnerConfig, *, broker: EventBroker | None = None) -> LoopWiring:
        """Read the prompt files now, on the calling thread."""
        return cls(config, config.resolved_workspace_prompt(), config.resolved_runner_prompt(), broker)

    def context(
        self, hub: IHubClient, *, engine: Engine | None = None, health_cache: HarnessHealthCache | None = None
    ) -> LoopContext:
        """Wire a :class:`LoopContext`; the caller owns the ``httpx.Client`` behind ``hub``,
        and the returned context's own ``usage_http_client`` (blizzard#436, hub:95) —
        closed the same way, once the caller is done with the context.

        Builds its own engine (kept separate from ``host``'s own, D4) unless ``engine`` is
        given — :class:`PeriodicDriver` passes its own so it can dispose it on thread exit
        (D5) without threading it through :class:`LoopContext` for a step to see.
        ``health_cache`` is the same exception ``engine`` is (blizzard#438): ``host`` passes
        the one instance it also gave the served app (``HostedApp.harness_health``), so a
        dashboard read and the loop's own registered availability read one shared, single
        source of truth rather than two independently-refreshing caches that can disagree."""
        config = self.config
        if engine is None:
            engine = create_engine_from_url(config.db_url)
        stores = build_stores(engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
        provider = WinterWorkspaceProvider(
            config.workspace_root, env_pool=config.workspace_envs, base_branch=config.base_branch
        )
        harnesses = build_production_harness_registry(config)
        # A startup guard: this composition's transcripts lane requires the default
        # harness's own binding to resolve one, not merely to be registered at all.
        harnesses.transcript_source(CLAUDE_CODE_HARNESS_ID)
        _clock = SystemClock()
        health_cache = health_cache or HarnessHealthCache(
            clock=_clock,
            probes=build_production_harness_health_probes(config),
            selftest_results=stores.selftest_results,
            configured_tiers={
                CLAUDE_CODE_HARNESS_ID: config.model_aliases,
                OPENCODE_HARNESS_ID: config.opencode_model_aliases,
            },
        )
        # The subscription-sampling seam (blizzard#436) — each declaration paired with its
        # resolved binding; an unknown provider selects `None` (declared, unsampled). Every
        # sampler shares one lazily-built HTTP client, owned by this context (blizzard#436,
        # hub:95), rather than opening its own.
        usage_http_client = _LazyUsageHttpClient()
        resolved_subscriptions = tuple(
            ResolvedSubscription(
                slug=declaration.slug,
                name=declaration.name,
                sample_interval_seconds=declaration.sample_interval_seconds,
                sampler=select_sampler(declaration, clock=_clock, http_client=usage_http_client),
            )
            for declaration in config.resolved_subscriptions()
        )
        # The per-lease harness-stdout directory (issue #58), created once here so a worker's
        # stdout redirect target always exists by the time a spawn/resume opens it.
        worker_stdout_dir = config.root / "worker-stdout"
        worker_stdout_dir.mkdir(parents=True, exist_ok=True)
        # The detached elicitation's own output directory (blizzard#443, D4) — load-bearing,
        # so it is always created, unlike `worker_stdout_dir`'s empty-disables convention.
        elicitation_output_dir = config.root / "elicitation-output"
        elicitation_output_dir.mkdir(parents=True, exist_ok=True)
        loop_config = LoopConfig(
            runner_id=config.runner_id,
            workspace_id=config.workspace_id,
            max_agents=config.max_agents,
            base_branch=config.base_branch,
            env_capacity=len(config.workspace_envs),  # issue #69
            public_url=config.public_url,  # issue #95 — this runner's own federation identity
            redirect_uris=config.redirect_uris,
            local_api_url=config.local_api_url,
            gates=config.gates,
            # The spawn cwd + static workspace-prompt fallback (issue #17). The prompt file is
            # resolved once here at loop-context build, not re-read per spawn.
            workspace_root=config.workspace_root,
            workspace_prompt=self.workspace_prompt,
            runner_prompt=self.runner_prompt,
            worker_stdout_dir=str(worker_stdout_dir),
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
            # The non-memoizing default (D4) — only `tick()` itself upgrades this per call.
            chunk_views=ReadThroughChunkViews(hub),
            provider=provider,
            subscriptions=resolved_subscriptions,
            usage_http_client=usage_http_client,
            process=LinuxProcessProbe(),
            worktree_git=SubprocessWorktreeGit(),
            # The check-runner seam (issue #114) — see `runner/loop/checks.py`.
            check_runner=SubprocessCheckRunner(env_passthrough=config.worker_env_passthrough),
            config=loop_config,
            worker_files=_worker_files,
            elicitation_files=_elicitation_files,
            usage=UsageRecorder(
                leases=stores.liveness,
                usage=stores.usage,
                clock=_clock,
                worker_files=_worker_files,
                workspace_root=config.workspace_root,
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
                leases=stores.lease_record,
                clock=_clock,
                provider=provider,
                worker_files=_worker_files,
                events=self.events,
            ),
            # The startup guard above already resolved the default harness's transcript
            # source (or raised) — this composition's transcripts lane is always wired.
            transcripts_wired=True,
            events=self.events,
            harnesses=harnesses,
            # Built once here (D4), long-lived across every tick `PeriodicDriver._run` drives on this context.
            harness_versions=HarnessVersionCache(clock=_clock),
            # Mirrors `harness_versions` (D4): built once, long-lived across every tick.
            harness_health=health_cache,
        )

    def tick_once(self) -> None:
        """Run one synchronous reconciliation tick — the CLI verb and e2e driver."""
        config = self.config
        with httpx.Client(base_url=config.hub_url, timeout=_HTTP_TIMEOUT, headers=config.auth_headers()) as client:
            ctx = self.context(HttpHubClient(client))
            try:
                tick(ctx)
            finally:
                ctx.usage_http_client.close()

    def backfill_transcripts(self, *, dry_run: bool, limit: int | None = None) -> TranscriptBackfillReport:
        """Run one transcript-backfill pass (blizzard#250) — the operator verb's own entry,
        wired here rather than at the CLI so the composition root stays the one place a
        context is built."""
        config = self.config
        with httpx.Client(base_url=config.hub_url, timeout=_HTTP_TIMEOUT, headers=config.auth_headers()) as client:
            ctx = self.context(HttpHubClient(client))
            try:
                return TranscriptBackfill(ctx).run(dry_run=dry_run, limit=limit)
            finally:
                ctx.usage_http_client.close()

    def reship_transcript(self, segment_id: str) -> TranscriptReshipReport:
        """Re-ship one already-imported segment — wired here for the reason above."""
        config = self.config
        with httpx.Client(base_url=config.hub_url, timeout=_HTTP_TIMEOUT, headers=config.auth_headers()) as client:
            ctx = self.context(HttpHubClient(client))
            try:
                return TranscriptBackfill(ctx).reship(segment_id)
            finally:
                ctx.usage_http_client.close()


@dataclass(frozen=True)
class ResumeMarking:
    """The ``host`` command's two restart-resume hooks (#12, #13), each over its own store.

    Store-only — no hub, no workspace provider."""

    stores: RunnerStores
    clock: IClock
    process: IProcessProbe

    def on_shutdown(self) -> int:
        """Mark in-flight leases as the daemon exits gracefully; an ungraceful ``kill -9``
        never reaches this path, which is the intended scope boundary."""
        return ResumeIntents(self.stores).mark_graceful(now=self.clock.now())

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
    ) -> None:
        # Wired eagerly on the constructing (``host``) thread so a missing prompt file
        # fails startup rather than the loop thread (`tests/test_runner_loop_build.py`).
        self._wiring = LoopWiring.of(config, broker=broker)
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="blizzard-runner-loop", daemon=True)
        self._client: httpx.Client | None = None
        # `host`'s own shared instance (blizzard#438, `HostedApp.harness_health`), so this
        # loop's registered availability and the served app's diagnostics read one cache.
        self._harness_health = harness_health

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Signal the loop to stop and wait for any in-flight tick to finish before returning.

        The join is **unbounded** on purpose: the graceful-shutdown resume marking runs
        right after this returns and must not race a live tick writing the same store. A
        tick cannot run forever — every seam it touches is timeout-bounded, including the
        judgement elicitation itself (blizzard#443): `judge` launches detached and returns
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
        # Built here, not inside `context()`, so this thread can dispose it on exit (D5) —
        # a gracefully stopped runner is a single-file store again. Inside the `try` below,
        # not before it: a raising `context()` call must still reach `finally`'s dispose.
        engine = create_engine_from_url(config.db_url)
        ctx: LoopContext | None = None
        try:
            ctx = self._wiring.context(HttpHubClient(self._client), engine=engine, health_cache=self._harness_health)
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
            engine.dispose()
            _log.info("reconciliation loop stopped", runner_id=config.runner_id)
