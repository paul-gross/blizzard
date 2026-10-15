"""FILL per binding — each binding claims its own project's work into its own workspace, up to its own agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.client import BindingDeclaration
from blizzard.runner.hub.served_projects import IReadServedProjectsRepository
from blizzard.runner.lifecycle.claim import ClaimConfig, ClaimContext, ClaimStores, ReadyQueue
from blizzard.runner.lifecycle.model import SlotBinding, binding_usage, free_slots
from blizzard.runner.lifecycle.routing import routing_map

_log = get_logger("blizzard.runner.loop")


class FillStores(ClaimStores, Protocol):
    @property
    def served_projects(self) -> IReadServedProjectsRepository: ...


class FillConfig(ClaimConfig, Protocol):
    @property
    def bindings(self) -> tuple[BindingDeclaration, ...] | None: ...


class FillContext(ClaimContext, Protocol):
    @property
    def stores(self) -> FillStores: ...
    @property
    def config(self) -> FillConfig: ...


@dataclass(frozen=True)
class BindingFill:
    """Walk the runner's bindings in configuration order, each peeking its own project once per free agent and
    stopping at the first empty answer. A binding with no free agent sends no peek, so a full project never stands in
    front of another's work; nothing is claimed while two bindings resolve to one project."""

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
        bindings = routing.claims()
        if not bindings:
            return
        used = binding_usage(
            [SlotBinding(workspace_id=b.workspace_id, project_id=b.project_id) for b in bindings],
            ctx.stores.environments.open_lease_holds(),
        )
        for binding, in_use in zip(bindings, used, strict=True):
            # Peek per attempt, not once per binding — the single-entry peek response leaves no cache to reuse.
            for _ in range(free_slots(binding.agents, in_use)):
                if not ReadyQueue.peeked(ctx, binding).claim_one():
                    break
