"""FILL across the runner's bindings — one shared agent count, claimed in the hub's ready-queue order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.runner.environments.free import IWorkspaceFreeEnvironment
from blizzard.runner.hub.client import BindingDeclaration
from blizzard.runner.hub.served_projects import IReadServedProjectsRepository
from blizzard.runner.lifecycle.claim import ClaimConfig, ClaimContext, ClaimStores, ReadyQueue
from blizzard.runner.lifecycle.model import free_slots, open_lease_count
from blizzard.runner.lifecycle.routing import routing_map

_log = get_logger("blizzard.runner.loop")


class FillStores(ClaimStores, Protocol):
    @property
    def served_projects(self) -> IReadServedProjectsRepository: ...


class FillConfig(ClaimConfig, Protocol):
    @property
    def bindings(self) -> tuple[BindingDeclaration, ...] | None: ...
    @property
    def agents(self) -> int: ...


class FillContext(ClaimContext, Protocol):
    @property
    def stores(self) -> FillStores: ...
    @property
    def config(self) -> FillConfig: ...
    @property
    def free_environments(self) -> IWorkspaceFreeEnvironment: ...


@dataclass(frozen=True)
class BindingFill:
    """Fill the runner's one agent count from the hub's ready queue. Each free agent sends one peek naming every
    routed project whose workspace has a free environment, and claims the returned chunk into that project's workspace;
    the hub's queue order alone picks the project. A workspace that proves out of environments drops its projects for
    the rest of the tick. Nothing is claimed while two bindings resolve to one project."""

    ctx: FillContext

    def fill(self) -> None:
        ctx = self.ctx
        routing = routing_map(ctx.config.bindings or (), ctx.stores.served_projects.served_projects_answer())
        if routing.conflicts:
            _log.warning(
                "no claims — two bindings resolve to one project",
                runner_name=ctx.config.runner_name,
                conflicts=[(c.project_id, list(c.entries)) for c in routing.conflicts],
            )
            return
        active = routing.claims()
        agents_free = free_slots(ctx.config.agents, open_lease_count(ctx.stores.environments.open_lease_holds()))
        while agents_free > 0:
            # Only a hint — acquisition stays authoritative under the pool lock.
            ready = tuple(b for b in active if ctx.free_environments.has_free_environment(b.workspace_id))
            if not ready:
                return
            # Peek per attempt, not once per fill — the single-entry peek response leaves no cache to reuse.
            queue = ReadyQueue.peeked(ctx, ready)
            if queue.claim_one():
                agents_free -= 1
            elif queue.env_bound_workspace_id is not None:
                active = tuple(b for b in active if b.workspace_id != queue.env_bound_workspace_id)
            else:
                return
