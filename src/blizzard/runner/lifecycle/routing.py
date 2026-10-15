"""Which binding a project's work is claimed through — derived, never stored.

The routing map joins the runner's bindings to the hub's latest answer to its declaration, by each binding's declared
entry. It is recomputed on every FILL, so every registration rebuilds it with no state of its own. An entry no project
answers to, or one not yet answered, routes nothing; two entries resolving to one project are a conflict, and while
one stands the runner claims nothing and its status names both."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.runner.hub.client import BindingDeclaration
from blizzard.runner.hub.served_projects import ServedProjectsAnswer

__all__ = ["RoutedBinding", "RoutingConflict", "RoutingMap", "routing_map"]


@domain_model
@dataclass(frozen=True)
class RoutedBinding:
    """One binding whose entry resolved: the entry as declared, the project id it names, its workspace and agents."""

    project: str
    project_id: str
    workspace_id: str
    agents: int


@domain_model
@dataclass(frozen=True)
class RoutingConflict:
    """Two or more declared entries that resolve to one project, in configuration order."""

    project_id: str
    entries: tuple[str, ...]


@domain_model
@dataclass(frozen=True)
class RoutingMap:
    """Each resolved binding in configuration order, and every conflict among them."""

    routes: tuple[RoutedBinding, ...]
    conflicts: tuple[RoutingConflict, ...] = ()

    def claims(self) -> tuple[RoutedBinding, ...]:
        """The bindings FILL claims through: every route, or none while any conflict stands."""
        return () if self.conflicts else self.routes

    def project_id(self, entry: str) -> str | None:
        """The project id ``entry`` resolved to, or ``None`` for an entry that routes nothing."""
        return next((route.project_id for route in self.routes if route.project == entry), None)


def routing_map(bindings: Sequence[BindingDeclaration], answer: ServedProjectsAnswer | None) -> RoutingMap:
    """Join ``bindings`` to ``answer`` by each binding's declared entry."""
    resolved = {p.entry: p.project_id for p in (answer.projects if answer is not None else ()) if p.resolved()}
    routes = tuple(
        RoutedBinding(
            project=binding.project,
            project_id=project_id,
            workspace_id=binding.workspace_id,
            agents=binding.agents,
        )
        for binding in bindings
        if (project_id := resolved.get(binding.project)) is not None
    )
    entries_by_project: dict[str, list[str]] = {}
    for route in routes:
        entries_by_project.setdefault(route.project_id, []).append(route.project)
    conflicts = tuple(
        RoutingConflict(project_id=project_id, entries=tuple(entries))
        for project_id, entries in entries_by_project.items()
        if len(entries) > 1
    )
    return RoutingMap(routes=routes, conflicts=conflicts)
