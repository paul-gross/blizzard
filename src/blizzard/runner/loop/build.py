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
from blizzard.runner.loop.steps import mark_resume_intents
from blizzard.runner.loop.tick import tick
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore

_log = get_logger("blizzard.runner.loop")

_HTTP_TIMEOUT = 30.0


def build_loop_context(config: RunnerConfig, hub: IHubClient) -> LoopContext:
    """Wire a :class:`LoopContext` from resolved config and an injected hub client.

    The hub client is passed in so the caller owns the ``httpx.Client`` lifecycle
    (a tick opens and closes it; the daemon keeps one for the driver's lifetime).
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
    )
    loop_config = LoopConfig(
        runner_id=config.runner_id,
        workspace_id=config.workspace_id,
        max_agents=config.max_agents,
        base_branch=config.base_branch,
        local_api_url=f"http://{config.host}:{config.port}",
        gates=config.gates,
    )
    return LoopContext(
        store=store,
        clock=SystemClock(),
        hub=hub,
        provider=provider,
        harness=harness,
        process=LinuxProcessProbe(),
        worktree_git=SubprocessWorktreeGit(),
        config=loop_config,
    )


def run_single_tick(config: RunnerConfig) -> None:
    """Run one synchronous reconciliation tick — the CLI verb and e2e driver."""
    with httpx.Client(base_url=config.hub_url, timeout=_HTTP_TIMEOUT) as client:
        ctx = build_loop_context(config, HttpHubClient(client))
        tick(ctx)


def mark_resume_intents_on_shutdown(config: RunnerConfig) -> int:
    """Mark in-flight leases for restart-resume as the daemon exits gracefully (D-082).

    Store-only — it needs neither the hub nor the workspace provider — so it opens just
    the runner store and delegates the which-leases decision to :func:`mark_resume_intents`.
    Called from the ``host`` command's shutdown path (a graceful SIGTERM lets uvicorn return
    and this run); an ungraceful ``kill -9`` never reaches it, which is the intended scope
    boundary (design/runner/loop.md)."""
    engine = create_engine_from_url(config.db_url)
    store = SqlAlchemyRunnerStore(engine)
    try:
        return mark_resume_intents(store, now=SystemClock().now())
    finally:
        engine.dispose()


class PeriodicDriver:
    """A background thread that ticks the loop on an interval (design/runner/loop.md ~30s).

    Owns its own ``httpx.Client`` for the driver's lifetime. A tick that raises is
    logged and swallowed so one bad pass never kills the daemon — the loop holds no
    state, so the next tick re-reconciles from the store.
    """

    def __init__(self, config: RunnerConfig, *, interval_seconds: float) -> None:
        self._config = config
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="blizzard-runner-loop", daemon=True)
        self._client: httpx.Client | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Signal the loop to stop and wait for any in-flight tick to finish before returning.

        The join is **unbounded** on purpose: the graceful-shutdown resume marking (D-082) runs
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
        self._client = httpx.Client(base_url=self._config.hub_url, timeout=_HTTP_TIMEOUT)
        ctx = build_loop_context(self._config, HttpHubClient(self._client))
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
