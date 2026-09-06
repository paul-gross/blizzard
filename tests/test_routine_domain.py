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
    IWriteRoutineScopeRepository,
    Routine,
    RoutineAuthoring,
    RoutineDefaultScopeUnlinkError,
    RoutineGraphUnresolvedError,
    RoutineNameImmutableError,
    RoutineNameTakenError,
    RoutineScopeMembership,
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


@dataclass
class _FakeRoutineScopeRepo:
    """A plain in-memory ``(routine_id, scope_slug)`` set — mirrors the real
    ``RoutineScopeStore``'s idempotent link/unlink (blizzard#488)."""

    linked: set[tuple[str, str]] = field(default_factory=set)

    def list_scopes(self, routine_id: str) -> list[str]:
        return sorted(slug for rid, slug in self.linked if rid == routine_id)

    def link(self, routine_id: str, scope_slug: str) -> None:
        self.linked.add((routine_id, scope_slug))

    def unlink(self, routine_id: str, scope_slug: str) -> None:
        self.linked.discard((routine_id, scope_slug))


def _as_write_routine_scopes(fake: _FakeRoutineScopeRepo) -> IWriteRoutineScopeRepository:
    return cast(IWriteRoutineScopeRepository, fake)


def _authoring(
    *,
    graphs: _FakeGraphs | None = None,
    scopes: _FakeScopeRepo | None = None,
    routines: _FakeRoutineRepo | None = None,
    routine_scopes: _FakeRoutineScopeRepo | None = None,
) -> tuple[RoutineAuthoring, _FakeRoutineRepo, _FakeScopeRepo, _FakeRoutineScopeRepo]:
    clock = FixedClock(instant=_T0)
    routines = routines or _FakeRoutineRepo()
    scopes = scopes or _FakeScopeRepo()
    graphs = graphs or _FakeGraphs()
    routine_scopes = routine_scopes or _FakeRoutineScopeRepo()
    authoring = RoutineAuthoring(
        routines=_as_write_routines(routines),
        graphs=_as_read_graphs(graphs),
        scope_registry=ScopeRegistry(scopes=_as_write_scopes(scopes), clock=clock),
        routine_scopes=_as_write_routine_scopes(routine_scopes),
        clock=clock,
    )
    return authoring, routines, scopes, routine_scopes


def test_create_mints_an_rtn_prefixed_id() -> None:
    authoring, _, _, _ = _authoring()

    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    assert routine.routine_id.startswith("rtn_")
    assert routine.name == "nightly"


def test_create_naming_an_existing_name_is_refused() -> None:
    authoring, _, _, _ = _authoring()
    authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    with pytest.raises(RoutineNameTakenError, match="nightly"):
        authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))


def test_create_naming_an_unresolved_graph_is_refused_naming_it() -> None:
    authoring, _, _, _ = _authoring(graphs=_FakeGraphs(resolvable={}))

    with pytest.raises(RoutineGraphUnresolvedError, match="alpha"):
        authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))


def test_create_naming_a_default_scope_mints_it() -> None:
    authoring, _, scopes, _ = _authoring()

    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    assert scopes.ensured == ["blizzard"]
    assert routine.default_scope_slug == "blizzard"


def test_edit_naming_a_different_name_is_refused_naming_the_current_one() -> None:
    authoring, _, _, _ = _authoring()
    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    with pytest.raises(RoutineNameImmutableError, match="nightly"):
        authoring.edit(routine, name="renamed", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))


def test_edit_changes_graph_scope_and_defaults() -> None:
    authoring, _, _, _ = _authoring(graphs=_FakeGraphs(resolvable={"alpha": _GRAPH, "beta": _GRAPH}))
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
    authoring, _, _, _ = _authoring()
    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    with pytest.raises(RoutineGraphUnresolvedError, match="ghost"):
        authoring.edit(routine, name="nightly", graph_name="ghost", default_scope_slug=ScopeSlug.parse("blizzard"))


# --- The routine_scopes join invariant (D8, blizzard#488) ----------------------------


def test_create_links_the_default_scope_into_the_routines_own_set() -> None:
    authoring, _, _, routine_scopes = _authoring()

    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    assert routine_scopes.list_scopes(routine.routine_id) == ["blizzard"]


def test_edit_links_the_new_default_scope_but_does_not_unlink_the_old_one() -> None:
    authoring, _, _, routine_scopes = _authoring()
    routine = authoring.create(name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("blizzard"))

    edited = authoring.edit(routine, name="nightly", graph_name="alpha", default_scope_slug=ScopeSlug.parse("other"))

    assert edited.default_scope_slug == "other"
    assert routine_scopes.list_scopes(routine.routine_id) == ["blizzard", "other"]


class TestRoutineScopeMembership:
    """``RoutineScopeMembership`` (unit tier, blizzard#488): link/unlink delegate to the
    repository, and unlink refuses a routine's own default scope (D8)."""

    def _routine(self, *, default_scope_slug: str = "blizzard") -> Routine:
        return Routine(
            routine_id="rtn_1",
            name="nightly",
            graph_name="alpha",
            default_scope_slug=default_scope_slug,
            created_at=_T0,
        )

    def _scope(self, slug: str) -> Scope:
        return Scope(slug=slug, description="", created_at=_T0)

    def test_link_delegates_to_the_repository(self) -> None:
        repo = _FakeRoutineScopeRepo()
        membership = RoutineScopeMembership(routine_scopes=_as_write_routine_scopes(repo))
        routine = self._routine()

        membership.link(routine, self._scope("other"))

        assert repo.list_scopes(routine.routine_id) == ["other"]

    def test_unlink_delegates_to_the_repository(self) -> None:
        repo = _FakeRoutineScopeRepo(linked={("rtn_1", "other")})
        membership = RoutineScopeMembership(routine_scopes=_as_write_routine_scopes(repo))
        routine = self._routine()

        membership.unlink(routine, self._scope("other"))

        assert repo.list_scopes(routine.routine_id) == []

    def test_unlink_the_routines_own_default_scope_is_refused(self) -> None:
        repo = _FakeRoutineScopeRepo(linked={("rtn_1", "blizzard")})
        membership = RoutineScopeMembership(routine_scopes=_as_write_routine_scopes(repo))
        routine = self._routine(default_scope_slug="blizzard")

        with pytest.raises(RoutineDefaultScopeUnlinkError, match="blizzard"):
            membership.unlink(routine, self._scope("blizzard"))

        assert repo.list_scopes(routine.routine_id) == ["blizzard"]

    def test_list_scopes_delegates_to_the_repository(self) -> None:
        repo = _FakeRoutineScopeRepo(linked={("rtn_1", "a"), ("rtn_1", "b"), ("rtn_2", "c")})
        membership = RoutineScopeMembership(routine_scopes=_as_write_routine_scopes(repo))

        assert membership.list_scopes(self._routine()) == ["a", "b"]
