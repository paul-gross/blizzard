"""Composition root for the reconciliation loop (``bzh:dependency-injection``).

The single place the loop's collaborators are constructed from resolved config and
injected into a :class:`LoopContext`: the runner store over the engine, the hub
client over an ``httpx.Client``, the winter workspace provider, the Claude Code
adapter, the process probe, and the worktree-git seam. ``run_single_tick`` is the
one-shot pass the ``blizzard runner tick`` CLI verb and the e2e drive;
:class:`PeriodicDriver` is the background timer the hosted daemon runs. Both open
the seam clients here and close them on exit, so no other code touches httpx or the
engine directly.
"""

from __future__ import annotations

import threading
from pathlib import Path

import httpx

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.hub import IHubClient
from blizzard.runner.loop.internal.http_hub import HttpHubClient
from blizzard.runner.loop.internal.subprocess_worktree_git import SubprocessWorktreeGit
from blizzard.runner.loop.process import LinuxProcessProbe
from blizzard.runner.loop.steps import mark_crash_resume_intents, mark_resume_intents
from blizzard.runner.loop.tick import tick
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.transcripts.internal.jsonl_transcript_repository import JsonlTranscriptRepository
from blizzard.runner.transcripts.repository import TranscriptErrorFactory

_log = get_logger("blizzard.runner.loop")

_HTTP_TIMEOUT = 30.0


def build_loop_context(
    config: RunnerConfig, hub: IHubClient, *, workspace_prompt: str, runner_prompt: str
) -> LoopContext:
    """Wire a :class:`LoopContext` from resolved config and an injected hub client.

    The hub client is passed in so the caller owns the ``httpx.Client`` lifecycle
    (a tick opens and closes it; the daemon keeps one for the driver's lifetime).

    ``workspace_prompt``/``runner_prompt`` are the caller's **already-resolved**
    values (``RunnerConfig.resolved_workspace_prompt()``/``resolved_runner_prompt()``),
    not re-derived here: both can raise ``ConfigError`` on a configured-but-missing
    prompt file, and resolving them on the caller's own thread — before this ever runs
    on :class:`PeriodicDriver`'s background thread — is what lets ``host`` turn that
    into a startup ``ClickException`` instead of a silently-killed loop thread.
    """
    engine = create_engine_from_url(config.db_url)
    store = SqlAlchemyRunnerStore(engine)
    provider = WinterWorkspaceProvider(
        config.workspace_root, env_pool=config.workspace_envs, base_branch=config.base_branch
    )
    harness = ClaudeCodeAdapter(
        binary=config.harness_binary,
        settings_path=config.worker_settings_path,
        permission_mode=config.harness_permission_mode,
        env_passthrough=config.worker_env_passthrough,
    )
    # The per-lease harness-stdout directory (issue #58) — under the runner's own data
    # directory, created once here (never inside the adapter), so a worker's stdout
    # redirect target always exists by the time a spawn/resume opens it.
    worker_stdout_dir = config.root / "worker-stdout"
    worker_stdout_dir.mkdir(parents=True, exist_ok=True)
    loop_config = LoopConfig(
        runner_id=config.runner_id,
        workspace_id=config.workspace_id,
        max_agents=config.max_agents,
        base_branch=config.base_branch,
        env_capacity=len(config.workspace_envs),  # issue #69 — the board's slot-bar total
        public_url=config.public_url,  # issue #95 — this runner's own federation identity
        redirect_uris=config.redirect_uris,
        local_api_url=f"http://{config.host}:{config.port}",
        gates=config.gates,
        # The spawn cwd + static workspace-prompt fallback (issue #17). The prompt file is
        # resolved once here at loop-context build, not re-read per spawn.
        workspace_root=config.workspace_root,
        workspace_prompt=workspace_prompt,
        runner_prompt=runner_prompt,
        worker_stdout_dir=str(worker_stdout_dir),
        chunk_cap_usd=config.chunk_cap_usd,
        runner_ceiling_usd=config.runner_ceiling_usd,
        runner_ceiling_window_hours=config.runner_ceiling_window_hours,
    )
    # The envelope-less usage fallback's transcript read (issue #58), mirroring
    # `runner/app.py`'s own construction of the panel's transcript seam (issue #29):
    # `transcripts_root` empty means Claude Code's own default, resolved once here.
    projects_root = config.transcripts_root or str(Path.home() / ".claude" / "projects")
    error_factory = TranscriptErrorFactory(get_logger("blizzard.runner.transcripts"))
    transcripts = JsonlTranscriptRepository(projects_root, error_factory)
    return LoopContext(
        store=store,
        clock=SystemClock(),
        hub=hub,
        provider=provider,
        harness=harness,
        process=LinuxProcessProbe(),
        worktree_git=SubprocessWorktreeGit(),
        config=loop_config,
        transcripts=transcripts,
    )


def run_single_tick(config: RunnerConfig) -> None:
    """Run one synchronous reconciliation tick — the CLI verb and e2e driver."""
    workspace_prompt = config.resolved_workspace_prompt()
    runner_prompt = config.resolved_runner_prompt()
    with httpx.Client(base_url=config.hub_url, timeout=_HTTP_TIMEOUT, headers=config.auth_headers()) as client:
        ctx = build_loop_context(
            config, HttpHubClient(client), workspace_prompt=workspace_prompt, runner_prompt=runner_prompt
        )
        tick(ctx)


def mark_resume_intents_on_shutdown(config: RunnerConfig) -> int:
    """Mark in-flight leases for restart-resume as the daemon exits gracefully.

    Store-only — it needs neither the hub nor the workspace provider — so it opens just
    the runner store and delegates the which-leases decision to :func:`mark_resume_intents`.
    Called from the ``host`` command's shutdown path (a graceful SIGTERM lets uvicorn return
    and this run); an ungraceful ``kill -9`` never reaches it, which is the intended scope
    boundary."""
    engine = create_engine_from_url(config.db_url)
    store = SqlAlchemyRunnerStore(engine)
    try:
        return mark_resume_intents(store, now=SystemClock().now())
    finally:
        engine.dispose()


def mark_crash_resume_intents_on_startup(config: RunnerConfig) -> int:
    """Detect crash-orphaned sessions at daemon startup and mark them for resume (#13).

    The ungraceful counterpart of :func:`mark_resume_intents_on_shutdown`: an involuntary
    ``kill -9`` / OOM / reboot never ran the shutdown marker, so ``host`` calls this once
    before starting the loop to find the interrupted sessions and route them to the same
    startup RESUME. It needs the runner store plus a process probe (the liveness check that
    tells a killed worker from an orphaned-but-alive one) — no hub, no workspace provider —
    so it opens just the store and delegates the which-leases decision to
    :func:`mark_crash_resume_intents`."""
    engine = create_engine_from_url(config.db_url)
    store = SqlAlchemyRunnerStore(engine)
    try:
        return mark_crash_resume_intents(store, process=LinuxProcessProbe(), now=SystemClock().now())
    finally:
        engine.dispose()


class PeriodicDriver:
    """A background thread that ticks the loop on an interval (~30s).

    Owns its own ``httpx.Client`` for the driver's lifetime. A tick that raises is
    logged and swallowed so one bad pass never kills the daemon — the loop holds no
    state, so the next tick re-reconciles from the store.
    """

    def __init__(self, config: RunnerConfig, *, interval_seconds: float) -> None:
        self._config = config
        self._interval = interval_seconds
        # Resolved eagerly here, on the constructing (``host``) thread, rather than
        # inside `_run` on the background loop thread it starts: a configured-but-
        # missing prompt file raises ``ConfigError`` from these calls, so the
        # constructor call in ``host`` — which turns it into a ``ClickException``
        # before any socket binds — is where that failure now surfaces, not a
        # silently-killed loop thread while uvicorn keeps serving ``/api/health`` 200s.
        self._workspace_prompt = config.resolved_workspace_prompt()
        self._runner_prompt = config.resolved_runner_prompt()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="blizzard-runner-loop", daemon=True)
        self._client: httpx.Client | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Signal the loop to stop and wait for any in-flight tick to finish before returning.

        The join is **unbounded** on purpose: the graceful-shutdown resume marking runs
        right after this returns and must not race a live tick writing the same store. A tick
        cannot run forever — every seam it touches is timeout-bounded (the hub client's
        ``_HTTP_TIMEOUT``), so the in-flight tick drains in at most about one tick's work and the
        thread then exits on the ``_stop`` check. systemd's ``TimeoutStopSec`` is the ultimate
        backstop: a wedged tick is SIGKILLed, which is simply the ungraceful-crash path (the
        unmarked workers fall back to REAP). A fixed timeout here, by contrast, could return while
        a slow tick was still running and let the marking race it."""
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        self._client = httpx.Client(
            base_url=self._config.hub_url, timeout=_HTTP_TIMEOUT, headers=self._config.auth_headers()
        )
        ctx = build_loop_context(
            self._config,
            HttpHubClient(self._client),
            workspace_prompt=self._workspace_prompt,
            runner_prompt=self._runner_prompt,
        )
        _log.info("reconciliation loop started", runner_id=self._config.runner_id, interval=self._interval)
        try:
            while not self._stop.is_set():
                try:
                    tick(ctx)
                except Exception as exc:  # a bad tick must not kill the daemon
                    _log.error("tick failed", detail=str(exc))
                self._stop.wait(self._interval)
        finally:
            self._client.close()
            _log.info("reconciliation loop stopped", runner_id=self._config.runner_id)
