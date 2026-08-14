"""The loop context — the ``(store, clock, seam clients)`` a step is a function of.

``bzh:steppable-loop`` requires each phase to be a pure function of its parameters,
reading the clock and every seam from them rather than a module global. This bundle is
that parameter object, plus the loop's static config.
"""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.runner.loop.checks import ICheckRunner
from blizzard.runner.loop.env_release import EnvironmentRelease
from blizzard.runner.loop.hub import IHubClient
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.loop.session import SessionResolver
from blizzard.runner.loop.usage import UsageRecorder
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles
from blizzard.runner.loop.worktree import IWorktreeGit
from blizzard.runner.store.repository import IWriteRunnerStore

#: The retry budget a node with no ``retries.max`` falls back to — a chosen constant:
#: two execution attempts before escalation to needs-human.
DEFAULT_RETRIES_MAX = 2


@dataclass(frozen=True)
class LoopConfig:
    """The reconciliation loop's static configuration."""

    runner_id: str
    workspace_id: str
    max_agents: int = 1
    base_branch: str = "main"
    #: The runner's configured environment-pool size (issue #69); ``None`` unreported.
    env_capacity: int | None = None
    #: This runner's own browser-reachable base URL (issue #95); empty registers no
    #: federation identity.
    public_url: str = ""
    #: The redirect URI(s) this runner presents to the hub's IdP authorize endpoint (#95).
    redirect_uris: tuple[str, ...] = ()
    default_retries_max: int = DEFAULT_RETRIES_MAX
    #: The runner's own local-API base URL, handed to a spawned worker as
    #: ``BLIZZARD_RUNNER_URL`` so its heartbeat hook posts back.
    local_api_url: str = "http://127.0.0.1:8431"
    #: The winter workspace root — the spawn cwd for every worker (issue #17), so it loads
    #: the workspace's shared context instead of starting below it in an env subdir.
    workspace_root: str = ""
    #: The static workspace prompt from config (issue #17), resolved once at ``host``
    #: startup — the fallback under the store's runtime override.
    workspace_prompt: str = ""
    #: The operator's override of the baked-in blizzard preamble (issue #103), resolved
    #: once at ``host`` startup. Empty means unset; there is no runtime override.
    runner_prompt: str = ""
    #: Node NAMES this runner imposes a human gate on — matched across all graphs and read
    #: at context build, so a config edit needs a restart, not just a new tick.
    gates: tuple[str, ...] = ()
    #: The directory the per-lease harness-stdout files live in (issue #58); empty means
    #: no redirect. A worker's envelope survives the process there for later read-back.
    worker_stdout_dir: str = ""
    #: The per-chunk spend cap (issue #61a); ``None`` means no cap.
    chunk_cap_usd: float | None = None
    #: The runner-wide spend ceiling (issue #61b); ``None`` means no ceiling.
    runner_ceiling_usd: float | None = None
    #: The runner ceiling's rolling window in hours; unused while the ceiling is ``None``.
    runner_ceiling_window_hours: float = 24.0
    #: The external-subscription-usage sample step's cadence in seconds (issue #218) —
    #: seconds that must elapse since the runner's last sampling attempt.
    external_usage_sample_interval_seconds: int = 300
    #: The session-context warn line; ``None`` disables the lane, reading no transcript at all.
    context_warn_tokens: int | None = None
    #: The context sample step's per-lease cadence in seconds; unused while the lane is off.
    context_sample_interval_seconds: int = 60
    #: This runner's runtime directory (``RunnerConfig.root``), absolute; empty means
    #: unresolved, and readers compose nothing from it rather than guessing (issue #251).
    runner_dir: str = ""
    #: The transcript outbound lane's own switch (``[transcripts] ship``, issue #246); off
    #: by default (D5) — the pump enqueues no delta while this is ``False``.
    transcripts_ship: bool = False


@dataclass(frozen=True)
class LoopContext:
    """Everything a step function reads — passed in, never module-global."""

    store: IWriteRunnerStore
    clock: IClock
    hub: IHubClient
    provider: IWorkspaceProvider
    harness: IHarnessAdapter
    process: IProcessProbe
    worktree_git: IWorktreeGit
    config: LoopConfig
    worker_files: WorkerStdoutFiles
    usage: UsageRecorder
    sessions: SessionResolver
    env_release: EnvironmentRelease
    #: The check-runner seam (issue #114) — ``None`` when not wired, so a node with no
    #: ``checks:`` still ticks; a node that declares ``checks:`` needs it.
    check_runner: ICheckRunner | None = None
    #: The harness transcript source (blizzard#245) — a declared field so the loop's own
    #: dependency is visible here; ``None`` when not wired.
    transcripts: IHarnessTranscriptSource | None = None
    #: The SSE broker (D2, blizzard#317) — shared with the served app when both are
    #: built by the same composer (the ``host`` verb, the e2e harness); ``None`` on
    #: ``blizzard runner tick`` and every other loop-only caller, where publishing is a
    #: no-op (Phase 3 wires the publish call sites; none is wired yet).
    events: EventBroker | None = None
