"""The loop context — the ``(store, clock, seam clients)`` a step is a function of.

``bzh:steppable-loop`` requires each phase to be a pure function of its parameters,
reading the clock and every seam from them rather than a module global — so tests
drive one step at a time against a virtual clock and injected fakes. This bundle is
that parameter object: the write store (the loop is the domain layer, it may
mutate), the injected clock, and the five seam clients (hub, workspace provider,
harness adapter, process probe, worktree git), plus the loop's static config.
"""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.loop.hub import IHubClient
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.loop.worktree import IWorktreeGit
from blizzard.runner.store.repository import IWriteRunnerStore
from blizzard.runner.transcripts.repository import IReadTranscriptRepository

#: The retry budget a node with no ``retries.max`` falls back to (a chosen constant,
#: not derived from a formula): an execution-attempt cap of 2 before escalation to
#: needs-human.
DEFAULT_RETRIES_MAX = 2


@dataclass(frozen=True)
class LoopConfig:
    """The reconciliation loop's static configuration."""

    runner_id: str
    workspace_id: str
    max_agents: int = 1
    base_branch: str = "main"
    #: The runner's configured environment-pool size (issue #69) — ``len(workspace_envs)``,
    #: mirrored once at composition from :class:`~blizzard.runner.config.RunnerConfig`. The
    #: loop reports it to the hub on each registration (the heartbeat) as the board's slot-bar
    #: ``total``; the loop consumes only the count, so the full pool stays with the provider.
    #: ``None`` means unreported — the hub stores null and the board omits the bar.
    env_capacity: int | None = None
    #: This runner's own browser-reachable base URL (issue #95), mirrored once at
    #: composition from :attr:`~blizzard.runner.config.RunnerConfig.public_url`.
    #: Reported to the hub on each registration alongside :attr:`redirect_uris`; empty
    #: means this runner registers no federation identity.
    public_url: str = ""
    #: The redirect URI(s) this runner presents to the hub's IdP authorize endpoint
    #: (issue #95), mirrored once from
    #: :attr:`~blizzard.runner.config.RunnerConfig.redirect_uris` (itself derived from
    #: ``public_url``).
    redirect_uris: tuple[str, ...] = ()
    default_retries_max: int = DEFAULT_RETRIES_MAX
    #: The runner's own local-API base URL, handed to a spawned worker as
    #: ``BLIZZARD_RUNNER_URL`` so its heartbeat hook posts back.
    local_api_url: str = "http://127.0.0.1:8431"
    #: The winter workspace root — the spawn cwd for every worker (issue #17), so it loads
    #: the workspace's shared context instead of starting below it in an env subdir.
    workspace_root: str = ""
    #: The static workspace prompt from config (issue #17), resolved once at ``host``
    #: startup. The fallback under the store's runtime override: the effective spawn
    #: preamble prose is ``store.workspace_prompt_override(workspace_id)`` when set, else this.
    workspace_prompt: str = ""
    #: The operator's override of the baked-in blizzard preamble (issue #103), resolved
    #: once at ``host`` startup from ``RunnerConfig.resolved_runner_prompt()``. Empty
    #: means unset — ``render_worker_preamble`` falls back to
    #: ``DEFAULT_BLIZZARD_PREAMBLE`` in that case. Config/startup only: unlike
    #: ``workspace_prompt`` there is no store-backed runtime override.
    runner_prompt: str = ""
    #: Node NAMES this runner imposes a human gate on: for a gated
    #: node the runner submits a Decision instead of a transition, so an operator dials
    #: their own HITL level without forking the fleet's graph. Matched by name across all
    #: graphs, read fresh from config at context build — true of every ``run_single_tick``
    #: (a fresh context per call, ``build.py``), but the hosted daemon's ``PeriodicDriver``
    #: builds one context and reuses it for its lifetime, so there a config edit needs a
    #: restart to take effect, not just a new tick.
    gates: tuple[str, ...] = ()
    #: The directory the per-lease harness-stdout files live in (issue #58) — resolved
    #: once at the composition root from the runner's own data directory, empty meaning
    #: "no redirect" (today's discard/inherit behavior, Phase 1's default). A worker's
    #: stdout is redirected to ``<this>/<lease_id>.<generation>.stdout`` so a killed/reaped
    #: worker's result envelope survives the process for ADVANCE's usage extraction to
    #: read back.
    worker_stdout_dir: str = ""
    #: The per-chunk spend cap (issue #61a), mirrored from ``RunnerConfig.chunk_cap_usd``.
    #: ``None`` means no cap — ADVANCE's step boundary (:func:`blizzard.runner.loop.steps.
    #: _park_on_cost_cap`) never parks a chunk on spend.
    chunk_cap_usd: float | None = None
    #: The runner-wide spend ceiling (issue #61b), mirrored from ``RunnerConfig.
    #: runner_ceiling_usd``. ``None`` means no ceiling — the tick's ceiling check
    #: (:func:`blizzard.runner.loop.steps.check_spend_ceiling`) never engages the local
    #: pause brake on spend alone.
    runner_ceiling_usd: float | None = None
    #: The runner ceiling's rolling window, in hours, mirrored from ``RunnerConfig.
    #: runner_ceiling_window_hours``. Unused while :attr:`runner_ceiling_usd` is ``None``.
    runner_ceiling_window_hours: float = 24.0


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
    #: The read-only transcript seam (issue #58's envelope-less usage fallback) — ``None``
    #: when not wired (every test that does not exercise the fallback), so the loop's
    #: other collaborators stay untouched by this addition.
    transcripts: IReadTranscriptRepository | None = None
