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
    #: Node NAMES this runner imposes a human gate on: for a gated
    #: node the runner submits a Decision instead of a transition, so an operator dials
    #: their own HITL level without forking the fleet's graph. Matched by name across all
    #: graphs, read fresh from config at context build — true of every ``run_single_tick``
    #: (a fresh context per call, ``build.py``), but the hosted daemon's ``PeriodicDriver``
    #: builds one context and reuses it for its lifetime, so there a config edit needs a
    #: restart to take effect, not just a new tick.
    gates: tuple[str, ...] = ()


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
