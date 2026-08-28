"""``RoutineAuthoring`` (unit tier, blizzard#389): create/edit over fake repositories —
a duplicate name is refused on create, a name change is refused on edit naming the
current name, an unresolved graph name is refused naming it, and naming a default scope
mints it through the real :class:`ScopeRegistry` — the
``tests/test_graph_lifecycle_service.py`` isolation shape (``bzh:domain-core``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.graph import Graph, IReadGraphRepository
from blizzard.hub.domain.routines import (
    IWriteRoutineRepository,
    Routine,
    RoutineAuthoring,
    RoutineGraphUnresolvedError,
    RoutineNameImmutableError,
    RoutineNameTakenError,
)
from blizzard.hub.domain.scopes import IWriteScopeRepository, Scope, ScopeRegistry, ScopeSlug

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_GRAPH = Graph(graph_id="gr_1", name="alpha", entry_node_id="nd_1", nodes=[], edges=[], created_at=_T0)


@dataclass
class _FakeGraphs:
    """Only ``get_enabled_by_name`` is live — the one seam ``RoutineAuthoring`` reads."""

    resolvable: dict[str, Graph] = field(default_factory=lambda: {"alpha": _GRAPH})

    def get_enabled_by_name(self, name: str) -> Graph | None:
        return self.resolvable.get(name)

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_read_graphs(fake: _FakeGraphs) -> IReadGraphRepository:
    return cast(IReadGraphRepository, fake)


@dataclass
class _FakeScopeRepo:
    ensured: list[str] = field(default_factory=list)

    def ensure(self, slug: str, *, description: str, at: datetime) -> Scope:
        self.ensured.append(slug)
        return Scope(slug=slug, description=description, created_at=at)

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_scopes(fake: _FakeScopeRepo) -> IWriteScopeRepository:
    return cast(IWriteScopeRepository, fake)


@dataclass
class _FakeRoutineRepo:
    by_id: dict[str, Routine] = field(default_factory=dict)
    by_name: dict[str, Routine] = field(default_factory=dict)
    edited: list[dict[str, Any]] = field(default_factory=list)

    def get(self, routine_id: str) -> Routine | None:
        return self.by_id.get(routine_id)

    def get_by_name(self, name: str) -> Routine | None:
        return self.by_name.get(name)

    def list_all(self) -> list[Routine]:
        return list(self.by_id.values())

    def create(self, routine: Routine) -> None:
        self.by_id[routine.routine_id] = routine
        self.by_name[routine.name] = routine

    def edit(
        self,
        routine_id: str,
        *,
        graph_name: str,
        default_scope_slug: str,
        default_model: list[str],
        default_effort: str | None,
    ) -> Routine:
        self.edited.append(
            {
                "routine_id": routine_id,
                "graph_name": graph_name,
                "default_scope_slug": default_scope_slug,
                "default_model": default_model,
                "default_effort": default_effort,
            }
        )
        current = self.by_id[routine_id]
        updated = Routine(
            routine_id=current.routine_id,
            name=current.name,
            graph_name=graph_name,
            default_scope_slug=default_scope_slug,
            created_at=current.created_at,
            default_model=default_model,
            default_effort=default_effort,
        )
        self.by_id[routine_id] = updated
        return updated


def _as_write_routines(fake: _FakeRoutineRepo) -> IWriteRoutineRepository:
    return cast(IWriteRoutineRepository, fake)


def _authoring(
    *, graphs: _FakeGraphs | None = None, scopes: _FakeScopeRepo | None = None, routines: _FakeRoutineRepo | None = None
) -> tuple[RoutineAuthoring, _FakeRoutineRepo, _FakeScopeRepo]:
    clock = FixedClock(instant=_T0)
    routines = routines or _FakeRoutineRepo()
    scopes = scopes or _FakeScopeRepo()
    graphs = graphs or _FakeGraphs()
    authoring = RoutineAuthoring(
        routines=_as_write_routines(routines),
        graphs=_as_read_graphs(graphs),
        scope_registry=ScopeRegistry(scopes=_as_write_scopes(scopes), clock=clock),
        clock=clock,
    )
    return authoring, routines, scopes


def test_create_mints_an_rtn_prefixed_id() -> None:
    authoring, _, _ = _authoring()

    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    assert routine.routine_id.startswith("rtn_")
    assert routine.name == "nightly"


def test_create_naming_an_existing_name_is_refused() -> None:
    authoring, _, _ = _authoring()
    authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    with pytest.raises(RoutineNameTakenError, match="nightly"):
        authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))


def test_create_naming_an_unresolved_graph_is_refused_naming_it() -> None:
    authoring, _, _ = _authoring(graphs=_FakeGraphs(resolvable={}))

    with pytest.raises(RoutineGraphUnresolvedError, match="alpha"):
        authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))


def test_create_naming_a_default_scope_mints_it() -> None:
    authoring, _, scopes = _authoring()

    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    assert scopes.ensured == ["blizzard"]
    assert routine.default_scope_slug == "blizzard"


def test_edit_naming_a_different_name_is_refused_naming_the_current_one() -> None:
    authoring, _, _ = _authoring()
    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    with pytest.raises(RoutineNameImmutableError, match="nightly"):
        authoring.edit(routine, name="renamed", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))


def test_edit_changes_graph_scope_and_defaults() -> None:
    authoring, _, _ = _authoring(graphs=_FakeGraphs(resolvable={"alpha": _GRAPH, "beta": _GRAPH}))
    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    edited = authoring.edit(
        routine,
        name="nightly",
        graph_name="beta",
        default_scope_slug=ScopeSlug.parse("other"),
        default_model=["blizzard:advanced"],
        default_effort="high",
    )

    assert edited.graph_name == "beta"
    assert edited.default_scope_slug == "other"
    assert edited.default_model == ["blizzard:advanced"]
    assert edited.default_effort == "high"


def test_edit_naming_an_unresolved_graph_is_refused_naming_it() -> None:
    authoring, _, _ = _authoring()
    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    with pytest.raises(RoutineGraphUnresolvedError, match="ghost"):
        authoring.edit(routine, name="nightly", graph_name="ghost", default_scope_slug=ScopeSlug.parse("blizzard"))
