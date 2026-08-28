"""Routine domain model — an operator-authored pointer at a graph, a default scope, and
run-defaults the hub hands back unresolved (issue #389).

``routine_id`` is a surrogate key: ``name``, the run/finding/proposal lineage, survives
independently of it (D1). :class:`RoutineAuthoring` mints an unseen default scope (D4)
and requires the named graph resolve to an enabled mint (D2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import ROUTINE_PREFIX, Id
from blizzard.hub.domain.graph import IReadGraphRepository
from blizzard.hub.domain.scopes import ScopeRegistry, ScopeSlug


class RoutineNameTakenError(ValueError):
    """A routine create names an already-existing routine name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"a routine named {name!r} already exists")
        self.name = name


class RoutineNameImmutableError(ValueError):
    """A routine edit tries to change the name — refused, naming the current one."""

    def __init__(self, current_name: str) -> None:
        super().__init__(f"a routine's name is immutable — currently named {current_name!r}")
        self.current_name = current_name


class RoutineGraphUnresolvedError(ValueError):
    """A routine create or edit names a graph with no enabled mint."""

    def __init__(self, graph_name: str) -> None:
        super().__init__(f"no enabled graph named {graph_name!r} exists")
        self.graph_name = graph_name


@dataclass(frozen=True)
class Routine:
    routine_id: str
    name: str
    graph_name: str
    default_scope_slug: str
    created_at: datetime
    default_model: list[str] = field(default_factory=list)
    default_effort: str | None = None


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadRoutineRepository(Protocol):
    """Read-only routine access. Controllers at the edges depend on this variant."""

    def get(self, routine_id: str) -> Routine | None: ...

    def get_by_name(self, name: str) -> Routine | None: ...

    def list_all(self) -> list[Routine]: ...


class IWriteRoutineRepository(IReadRoutineRepository, Protocol):
    """Read-write routine access. Only the domain layer depends on this variant."""

    def create(self, routine: Routine) -> None:
        """Insert a routine row. Uniqueness of ``name`` is enforced by
        :class:`RoutineAuthoring` before this is called (D7's shape) — the store's own
        ``uq_routines_name`` is a backstop, not the refusal path."""
        ...

    def edit(
        self,
        routine_id: str,
        *,
        graph_name: str,
        default_scope_slug: str,
        default_model: list[str],
        default_effort: str | None,
    ) -> Routine:
        """Change everything but ``name``/``routine_id`` in place (D3)."""
        ...


class RoutineAuthoring:
    """Create and edit a routine, minting its default scope on demand (D4, issue #389)."""

    def __init__(
        self,
        *,
        routines: IWriteRoutineRepository,
        graphs: IReadGraphRepository,
        scope_registry: ScopeRegistry,
        clock: IClock,
    ) -> None:
        self._routines = routines
        self._graphs = graphs
        self._scope_registry = scope_registry
        self._clock = clock

    def create(
        self,
        *,
        name: str,
        graph_name: str,
        default_scope_slug: ScopeSlug,
        default_model: list[str] | None = None,
        default_effort: str | None = None,
    ) -> Routine:
        if self._routines.get_by_name(name) is not None:
            raise RoutineNameTakenError(name)
        self._ensure_graph_resolves(graph_name)
        scope = self._scope_registry.ensure(default_scope_slug)
        routine = Routine(
            routine_id=Id.mint(ROUTINE_PREFIX, self._clock).value,
            name=name,
            graph_name=graph_name,
            default_scope_slug=scope.slug,
            created_at=self._clock.now(),
            default_model=list(default_model or []),
            default_effort=default_effort,
        )
        self._routines.create(routine)
        return routine

    def edit(
        self,
        routine: Routine,
        *,
        name: str,
        graph_name: str,
        default_scope_slug: ScopeSlug,
        default_model: list[str] | None = None,
        default_effort: str | None = None,
    ) -> Routine:
        if name != routine.name:
            raise RoutineNameImmutableError(routine.name)
        self._ensure_graph_resolves(graph_name)
        scope = self._scope_registry.ensure(default_scope_slug)
        return self._routines.edit(
            routine.routine_id,
            graph_name=graph_name,
            default_scope_slug=scope.slug,
            default_model=list(default_model or []),
            default_effort=default_effort,
        )

    def _ensure_graph_resolves(self, graph_name: str) -> None:
        if self._graphs.get_enabled_by_name(graph_name) is None:
            raise RoutineGraphUnresolvedError(graph_name)
