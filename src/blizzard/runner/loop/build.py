"""Composition root for the reconciliation loop (``bzh:dependency-injection``).

The single place the loop's collaborators are constructed from resolved config and
injected into a :class:`LoopContext`. Both ``run_single_tick`` and
:class:`PeriodicDriver` open the seam clients here and close them on exit.
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
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.transcript import TranscriptErrorFactory as HarnessTranscriptErrorFactory
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.hub import IHubClient
from blizzard.runner.loop.internal.http_hub import HttpHubClient
from blizzard.runner.loop.internal.subprocess_check_runner import SubprocessCheckRunner
from blizzard.runner.loop.internal.subprocess_worktree_git import SubprocessWorktreeGit
from blizzard.runner.loop.process import LinuxProcessProbe
from blizzard.runner.loop.steps import mark_crash_resume_intents, mark_resume_intents
from blizzard.runner.loop.tick import tick
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore

_log = get_logger("blizzard.runner.loop")

_HTTP_TIMEOUT = 30.0


def build_loop_context(
    config: RunnerConfig, hub: IHubClient, *, workspace_prompt: str, runner_prompt: str
) -> LoopContext:
    """Wire a :class:`LoopContext` from resolved config and an injected hub client.

    The hub client is passed in so the caller owns the ``httpx.Client`` lifecycle. The
    prompts are **already-resolved** values: resolving them on the caller's own thread
    turns a missing prompt file into a startup error (``tests/test_pin_runner_loop.py``)."""
    engine = create_engine_from_url(config.db_url)
    store = SqlAlchemyRunnerStore(engine)
    provider = WinterWorkspaceProvider(
        config.workspace_root, env_pool=config.workspace_envs, base_branch=config.base_branch
    )
    # Resolved once here for both readers (issue #58, blizzard#245; see also
    # `runner/app.py`, issue #29): `transcripts_root` empty means Claude Code's own default.
    projects_root = config.transcripts_root or str(Path.home() / ".claude" / "projects")
    harness_transcript_source = ClaudeCodeTranscriptSource(
        projects_root, HarnessTranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript"))
    )
    harness = ClaudeCodeAdapter(
        binary=config.harness_binary,
        settings_path=config.worker_settings_path,
        permission_mode=config.harness_permission_mode,
        env_passthrough=config.worker_env_passthrough,
        model_aliases=config.model_aliases,
        effort_aliases=config.effort_aliases,
        credentials_path=config.external_usage_credentials_path,
        transcript_source=harness_transcript_source,
    )
    # The per-lease harness-stdout directory (issue #58), created once here so a worker's
    # stdout redirect target always exists by the time a spawn/resume opens it.
    worker_stdout_dir = config.root / "worker-stdout"
    worker_stdout_dir.mkdir(parents=True, exist_ok=True)
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
        workspace_prompt=workspace_prompt,
        runner_prompt=runner_prompt,
        worker_stdout_dir=str(worker_stdout_dir),
        chunk_cap_usd=config.chunk_cap_usd,
        runner_ceiling_usd=config.runner_ceiling_usd,
        runner_ceiling_window_hours=config.runner_ceiling_window_hours,
        external_usage_sample_interval_seconds=config.external_usage_sample_interval_seconds,
        runner_dir=str(config.root),
    )
    return LoopContext(
        store=store,
        clock=SystemClock(),
        hub=hub,
        provider=provider,
        harness=harness,
        process=LinuxProcessProbe(),
        worktree_git=SubprocessWorktreeGit(),
        # The check-runner seam (issue #114) — see `runner/loop/checks.py`.
        check_runner=SubprocessCheckRunner(env_passthrough=config.worker_env_passthrough),
        config=loop_config,
        # The same source injected into `harness` above, declared here too so the loop's
        # direct readers don't reach through `ctx.harness` for it.
        transcripts=harness_transcript_source,
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

    Store-only — no hub, no workspace provider. Called from the ``host`` command's graceful
    shutdown path; an ungraceful ``kill -9`` never reaches it, which is the intended scope
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
    kill never ran the shutdown marker, so ``host`` calls this once before starting the
    loop. Needs the runner store plus a process probe — no hub, no workspace provider."""
    engine = create_engine_from_url(config.db_url)
    store = SqlAlchemyRunnerStore(engine)
    try:
        return mark_crash_resume_intents(store, process=LinuxProcessProbe(), now=SystemClock().now())
    finally:
        engine.dispose()


class PeriodicDriver:
    """A background thread that ticks the loop on an interval (~30s).

    Owns its own ``httpx.Client`` for the driver's lifetime. A tick that raises is logged
    and swallowed so one bad pass never kills the daemon."""

    def __init__(self, config: RunnerConfig, *, interval_seconds: float) -> None:
        self._config = config
        self._interval = interval_seconds
        # Resolved eagerly on the constructing (``host``) thread so a missing prompt file
        # fails startup rather than the loop thread (`tests/test_runner_loop_build.py`).
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
        right after this returns and must not race a live tick writing the same store. A
        tick cannot run forever — every seam it touches is timeout-bounded."""
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
