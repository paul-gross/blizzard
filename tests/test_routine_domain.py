"""``RoutineAuthoring``, ``RoutineScopeMembership``, and ``RoutineLifecycle`` (unit tier) over
fake repositories — a duplicate name is refused on create, the sparse edit's name
immutability and null refusals, revisions and change rows, writes that change nothing
writing nothing, an unresolved graph name refused naming it, and naming an unseen default
scope minting it through the real :class:`ScopeRegistry` — the
``tests/test_graph_lifecycle_service.py`` isolation shape (``bzh:domain-core``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, Door, FieldChange, RecordKind
from blizzard.hub.domain.config.work_sources import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.garden.routines import (
    IWriteRoutineRepository,
    IWriteRoutineScopeRepository,
    Routine,
    RoutineAuthoring,
    RoutineDefaultScopeUnlinkError,
    RoutineEdit,
    RoutineGraphUnresolvedError,
    RoutineLifecycle,
    RoutineNameImmutableError,
    RoutineNameTakenError,
    RoutineScopeMembership,
    require_graph_change_resolves,
)
from blizzard.hub.domain.garden.scopes import IWriteScopeRepository, Scope, ScopeMint, ScopeRegistry, ScopeSlug
from blizzard.hub.domain.graph.harnesses import InvalidHarnesses
from blizzard.hub.domain.graph.model import Graph, IReadGraphRepository

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


_CTX = ChangeContext(actor="usr_1", door=Door.CLI)


@dataclass
class _FakeScopeRepo:
    """Only ``get`` is live — a routine's default-scope mint commits through the routine repo."""

    stored: dict[str, Scope] = field(default_factory=dict)

    def get(self, slug: str) -> Scope | None:
        return self.stored.get(slug)

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _as_write_scopes(fake: _FakeScopeRepo) -> IWriteScopeRepository:
    return cast(IWriteScopeRepository, fake)


@dataclass
class _FakeRoutineScopeRepo:
    """A plain in-memory ``(routine_id, scope_slug)`` set; link/unlink record their change."""

    linked: set[tuple[str, str]] = field(default_factory=set)
    changes: list[tuple[int, ConfigChange]] = field(default_factory=list)

    def list_scopes(self, routine_id: str) -> list[str]:
        return sorted(slug for rid, slug in self.linked if rid == routine_id)

    def list_routines(self, scope_slug: str) -> list[str]:
        return sorted(rid for rid, slug in self.linked if slug == scope_slug)

    def link(self, routine: Routine, scope_slug: str, *, from_revision: int, change: ConfigChange) -> Routine:
        self.linked.add((routine.routine_id, scope_slug))
        self.changes.append((from_revision, change))
        return routine

    def unlink(self, routine: Routine, scope_slug: str, *, from_revision: int, change: ConfigChange) -> Routine:
        self.linked.discard((routine.routine_id, scope_slug))
        self.changes.append((from_revision, change))
        return routine


def _as_write_routine_scopes(fake: _FakeRoutineScopeRepo) -> IWriteRoutineScopeRepository:
    return cast(IWriteRoutineScopeRepository, fake)


@dataclass
class _FakeRoutineRepo:
    """Stores routines in memory; each write records its change and any default-scope
    mint, and links the default into ``joins`` — the one-transaction shape of the real store."""

    joins: _FakeRoutineScopeRepo = field(default_factory=_FakeRoutineScopeRepo)
    by_id: dict[str, Routine] = field(default_factory=dict)
    by_name: dict[str, Routine] = field(default_factory=dict)
    changes: list[ConfigChange] = field(default_factory=list)
    mints: list[ScopeMint] = field(default_factory=list)
    updates: list[int] = field(default_factory=list)
    recorded: list[tuple[str, bool, int, str]] = field(default_factory=list)

    def get(self, routine_id: str) -> Routine | None:
        return self.by_id.get(routine_id)

    def get_by_name(self, name: str) -> Routine | None:
        return self.by_name.get(name)

    def _write(self, routine: Routine, change: ConfigChange, scope_mint: ScopeMint | None) -> Routine:
        self.by_id[routine.routine_id] = routine
        self.by_name[routine.name] = routine
        self.joins.linked.add((routine.routine_id, routine.default_scope_slug))
        self.changes.append(change)
        if scope_mint is not None:
            self.mints.append(scope_mint)
        return routine

    def create(self, routine: Routine, *, change: ConfigChange, scope_mint: ScopeMint | None) -> Routine:
        return self._write(routine, change, scope_mint)

    def update(
        self, routine: Routine, *, from_revision: int, change: ConfigChange, scope_mint: ScopeMint | None
    ) -> Routine:
        self.updates.append(from_revision)
        return self._write(routine, change, scope_mint)

    def record_lifecycle(
        self, routine: Routine, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
    ) -> Routine:
        self.recorded.append((routine.routine_id, retired, from_revision, by))
        self.changes.append(change)
        return routine


def _as_write_routines(fake: _FakeRoutineRepo) -> IWriteRoutineRepository:
    return cast(IWriteRoutineRepository, fake)


def _authoring(
    *,
    graphs: _FakeGraphs | None = None,
    scopes: _FakeScopeRepo | None = None,
) -> tuple[RoutineAuthoring, _FakeRoutineRepo, _FakeScopeRepo, _FakeRoutineScopeRepo]:
    clock = FixedClock(instant=_T0)
    routines = _FakeRoutineRepo()
    scopes = scopes or _FakeScopeRepo()
    authoring = RoutineAuthoring(
        routines=_as_write_routines(routines),
        graphs=_as_read_graphs(graphs or _FakeGraphs()),
        scope_registry=ScopeRegistry(scopes=_as_write_scopes(scopes), clock=clock),
        clock=clock,
    )
    return authoring, routines, scopes, routines.joins


def _create(authoring: RoutineAuthoring, **overrides: Any) -> Routine:
    fields: dict[str, Any] = {
        "name": "nightly",
        "graph_name": "alpha",
        "default_scope_slug": ScopeSlug.parse("blizzard"),
        "ctx": _CTX,
    }
    fields.update(overrides)
    return authoring.create(**fields)


def test_create_mints_an_rtn_prefixed_id() -> None:
    authoring, _, _, _ = _authoring()

    routine = _create(authoring)

    assert routine.routine_id.startswith("rtn_")
    assert routine.name == "nightly"


def test_create_writes_revision_one_and_a_create_change_keyed_on_the_name() -> None:
    authoring, routines, _, _ = _authoring()

    routine = _create(authoring, default_effort="high")

    [change] = routines.changes
    assert routine.revision == 1
    assert (change.record_kind, change.record_key, change.revision, change.op) == (
        RecordKind.ROUTINE,
        "nightly",
        1,
        ChangeOp.CREATE,
    )
    assert FieldChange("default_effort", None, "high") in change.diff
    assert (change.actor, change.door) == ("usr_1", Door.CLI)


def test_create_naming_an_existing_name_is_refused() -> None:
    authoring, _, _, _ = _authoring()
    _create(authoring)

    with pytest.raises(RoutineNameTakenError, match="nightly"):
        _create(authoring)


def test_create_naming_an_unresolved_graph_is_refused_naming_it() -> None:
    authoring, _, _, _ = _authoring(graphs=_FakeGraphs(resolvable={}))

    with pytest.raises(RoutineGraphUnresolvedError, match="alpha"):
        _create(authoring)


def test_create_naming_an_unseen_default_scope_mints_it_in_the_same_write() -> None:
    authoring, routines, _, _ = _authoring()

    routine = _create(authoring)

    [(scope, change)] = routines.mints
    assert (scope.slug, change.record_kind, change.op) == ("blizzard", RecordKind.SCOPE, ChangeOp.CREATE)
    assert routine.default_scope_slug == "blizzard"


def test_create_naming_a_stored_default_scope_mints_nothing() -> None:
    stored = Scope(slug="blizzard", description="", created_at=_T0)
    authoring, routines, _, _ = _authoring(scopes=_FakeScopeRepo(stored={"blizzard": stored}))

    _create(authoring)

    assert routines.mints == []


def test_create_with_no_harnesses_preference_mints_empty() -> None:
    authoring, _, _, _ = _authoring()

    assert _create(authoring).default_harnesses == []


def test_create_carries_a_harnesses_preference() -> None:
    authoring, _, _, _ = _authoring()

    routine = _create(authoring, default_harnesses=["claude_code", "codex"])

    assert routine.default_harnesses == ["claude_code", "codex"]


@pytest.mark.parametrize("entries", [[" "], ["claude_code", " claude_code "]])
def test_create_refuses_invalid_harnesses_before_minting_scope(entries: list[str]) -> None:
    authoring, routines, _, _ = _authoring()

    with pytest.raises(InvalidHarnesses):
        _create(authoring, default_harnesses=entries)

    assert routines.by_id == {}
    assert routines.mints == []


def test_create_normalizes_harnesses() -> None:
    authoring, _, _, _ = _authoring()

    routine = _create(authoring, default_harnesses=[" claude_code ", "codex"])

    assert routine.default_harnesses == ["claude_code", "codex"]


# --- The sparse edit ------------------------------------------------------------


def test_edit_naming_a_different_name_is_refused_naming_the_current_one() -> None:
    authoring, _, _, _ = _authoring()
    routine = _create(authoring)

    with pytest.raises(RoutineNameImmutableError, match="nightly"):
        authoring.edit(routine, RoutineEdit(name="renamed"), _CTX)


def test_edit_restating_the_name_alone_writes_nothing() -> None:
    authoring, routines, _, _ = _authoring()
    routine = _create(authoring)

    assert authoring.edit(routine, RoutineEdit(name="nightly"), _CTX) is routine
    assert routines.updates == []


def test_edit_of_one_field_leaves_the_others_and_moves_the_revision_by_one() -> None:
    authoring, routines, _, _ = _authoring()
    routine = _create(authoring, default_model=["blizzard:advanced"])

    edited = authoring.edit(routine, RoutineEdit(default_effort="high"), _CTX)

    assert (edited.default_effort, edited.default_model, edited.graph_name) == ("high", ["blizzard:advanced"], "alpha")
    assert edited.revision == 2
    assert routines.updates == [1]
    assert routines.changes[-1].diff == (FieldChange("default_effort", None, "high"),)


def test_edit_of_the_full_payload_sets_every_field() -> None:
    authoring, _, _, _ = _authoring(graphs=_FakeGraphs(resolvable={"alpha": _GRAPH, "beta": _GRAPH}))
    routine = _create(authoring)

    edited = authoring.edit(
        routine,
        RoutineEdit(
            name="nightly",
            graph_name="beta",
            default_scope_slug=ScopeSlug.parse("other"),
            default_model=["blizzard:advanced"],
            default_effort="high",
            default_harnesses=["claude_code"],
        ),
        _CTX,
    )

    assert edited.graph_name == "beta"
    assert edited.default_scope_slug == "other"
    assert edited.default_model == ["blizzard:advanced"]
    assert edited.default_effort == "high"
    assert edited.default_harnesses == ["claude_code"]


def test_edit_clears_default_effort_with_null() -> None:
    authoring, _, _, _ = _authoring()
    routine = _create(authoring, default_effort="high")

    assert authoring.edit(routine, RoutineEdit(default_effort=None), _CTX).default_effort is None


@pytest.mark.parametrize("name", ["graph_name", "default_scope_slug", "default_model", "default_harnesses"])
def test_edit_refuses_null_on_a_required_field_naming_it(name: str) -> None:
    authoring, _, _, _ = _authoring()
    routine = _create(authoring)

    with pytest.raises(ConfigFieldError) as exc_info:
        authoring.edit(routine, RoutineEdit(**{name: None}), _CTX)
    assert exc_info.value.field == name


def test_edit_with_a_stale_if_match_is_refused_naming_the_current_revision() -> None:
    authoring, _, _, _ = _authoring()
    routine = _create(authoring)

    with pytest.raises(ConfigRevisionConflict) as exc_info:
        authoring.edit(routine, RoutineEdit(default_effort="high"), _CTX, if_match=7)
    assert exc_info.value.current == 1


@pytest.mark.parametrize("entries", [["  "], ["codex", " codex "]])
def test_edit_refuses_invalid_harnesses_before_minting_scope(entries: list[str]) -> None:
    authoring, routines, _, _ = _authoring()
    routine = _create(authoring)

    with pytest.raises(InvalidHarnesses):
        authoring.edit(
            routine, RoutineEdit(default_scope_slug=ScopeSlug.parse("other"), default_harnesses=entries), _CTX
        )

    assert routines.updates == []
    assert [scope.slug for scope, _ in routines.mints] == ["blizzard"]


def test_edit_naming_an_unresolved_graph_is_refused_naming_it() -> None:
    authoring, _, _, _ = _authoring()
    routine = _create(authoring)

    with pytest.raises(RoutineGraphUnresolvedError, match="ghost"):
        authoring.edit(routine, RoutineEdit(graph_name="ghost"), _CTX)


def test_edit_restating_a_since_retired_graph_writes_the_other_fields() -> None:
    graphs = _FakeGraphs()
    authoring, _, _, _ = _authoring(graphs=graphs)
    routine = _create(authoring)
    graphs.resolvable.clear()

    edited = authoring.edit(routine, RoutineEdit(graph_name="alpha", default_effort="high"), _CTX)

    assert (edited.default_effort, edited.revision) == ("high", routine.revision + 1)


def test_the_graph_check_refuses_a_create_or_a_change_to_an_unresolved_name_only() -> None:
    def never(_: str) -> bool:
        raise AssertionError("an unchanged name must not be resolved")

    with pytest.raises(RoutineGraphUnresolvedError, match="ghost"):
        require_graph_change_resolves(None, "ghost", lambda _: False)
    with pytest.raises(RoutineGraphUnresolvedError, match="ghost"):
        require_graph_change_resolves("alpha", "ghost", lambda _: False)
    require_graph_change_resolves(None, "alpha", lambda _: True)
    require_graph_change_resolves("alpha", "alpha", never)


def test_edit_without_a_graph_skips_graph_resolution() -> None:
    graphs = _FakeGraphs()
    authoring, _, _, _ = _authoring(graphs=graphs)
    routine = _create(authoring)
    graphs.resolvable.clear()

    assert authoring.edit(routine, RoutineEdit(default_effort="high"), _CTX).default_effort == "high"


# --- The routine_scopes join invariant ----------------------------


def test_create_links_the_default_scope_into_the_routines_own_set() -> None:
    authoring, _, _, routine_scopes = _authoring()

    routine = _create(authoring)

    assert routine_scopes.list_scopes(routine.routine_id) == ["blizzard"]


def test_edit_links_the_new_default_scope_but_does_not_unlink_the_old_one() -> None:
    authoring, routines, _, routine_scopes = _authoring()
    routine = _create(authoring)

    edited = authoring.edit(routine, RoutineEdit(default_scope_slug=ScopeSlug.parse("other")), _CTX)

    assert edited.default_scope_slug == "other"
    assert routine_scopes.list_scopes(routine.routine_id) == ["blizzard", "other"]
    assert [scope.slug for scope, _ in routines.mints] == ["blizzard", "other"]


def _routine(**overrides: object) -> Routine:
    fields: dict[str, object] = {
        "routine_id": "rtn_1",
        "name": "nightly",
        "graph_name": "alpha",
        "default_scope_slug": "blizzard",
        "created_at": _T0,
    }
    fields.update(overrides)
    return Routine(**fields)  # type: ignore[arg-type]


class TestRoutineScopeMembership:
    """``RoutineScopeMembership`` (unit tier): link/unlink are routine edits of ``scopes``,
    a no-op writes nothing, and unlink refuses a routine's own default scope."""

    def _scope(self, slug: str) -> Scope:
        return Scope(slug=slug, description="", created_at=_T0)

    def _membership(self, repo: _FakeRoutineScopeRepo) -> RoutineScopeMembership:
        return RoutineScopeMembership(routine_scopes=_as_write_routine_scopes(repo), clock=FixedClock(instant=_T0))

    def test_link_is_an_edit_of_scopes_moving_the_revision(self) -> None:
        repo = _FakeRoutineScopeRepo(linked={("rtn_1", "blizzard")})

        linked = self._membership(repo).link(_routine(revision=2), self._scope("other"), _CTX)

        assert repo.list_scopes("rtn_1") == ["blizzard", "other"]
        [(from_revision, change)] = repo.changes
        assert (linked.revision, from_revision, change.op, change.record_key) == (3, 2, ChangeOp.EDIT, "nightly")
        assert change.diff == (FieldChange("scopes", ["blizzard"], ["blizzard", "other"]),)

    def test_link_of_a_linked_scope_writes_nothing(self) -> None:
        repo = _FakeRoutineScopeRepo(linked={("rtn_1", "other")})

        self._membership(repo).link(_routine(), self._scope("other"), _CTX)

        assert repo.changes == []

    def test_unlink_removes_the_scope(self) -> None:
        repo = _FakeRoutineScopeRepo(linked={("rtn_1", "other")})

        self._membership(repo).unlink(_routine(), self._scope("other"), _CTX)

        assert repo.list_scopes("rtn_1") == []
        assert repo.changes[0][1].diff == (FieldChange("scopes", ["other"], []),)

    def test_unlink_of_an_unlinked_scope_writes_nothing(self) -> None:
        repo = _FakeRoutineScopeRepo()

        self._membership(repo).unlink(_routine(), self._scope("other"), _CTX)

        assert repo.changes == []

    def test_link_with_a_stale_if_match_is_refused(self) -> None:
        repo = _FakeRoutineScopeRepo()

        with pytest.raises(ConfigRevisionConflict):
            self._membership(repo).link(_routine(), self._scope("other"), _CTX, if_match=5)

    def test_unlink_the_routines_own_default_scope_is_refused(self) -> None:
        repo = _FakeRoutineScopeRepo(linked={("rtn_1", "blizzard")})

        with pytest.raises(RoutineDefaultScopeUnlinkError, match="blizzard"):
            self._membership(repo).unlink(_routine(), self._scope("blizzard"), _CTX)

        assert repo.list_scopes("rtn_1") == ["blizzard"]


# --- RoutineLifecycle (unit tier) — the ScopeLifecycle shape ------------


def test_lifecycle_retire_records_the_fact_by_and_the_change_actor() -> None:
    repo = _FakeRoutineRepo()
    lifecycle = RoutineLifecycle(routines=_as_write_routines(repo), clock=FixedClock(instant=_T0))

    retired = lifecycle.retire(_routine(), _CTX, by="operator")

    assert repo.recorded == [("rtn_1", True, 1, "operator")]
    assert (retired.retired, retired.revision) == (True, 2)
    [change] = repo.changes
    assert (change.actor, change.op, change.diff) == ("usr_1", ChangeOp.RETIRE, (FieldChange("retired", False, True),))


def test_lifecycle_enable_records_a_retired_false_fact() -> None:
    repo = _FakeRoutineRepo()
    lifecycle = RoutineLifecycle(routines=_as_write_routines(repo), clock=FixedClock(instant=_T0))

    lifecycle.enable(_routine(retired=True), _CTX, by="operator")

    assert repo.recorded == [("rtn_1", False, 1, "operator")]


@pytest.mark.parametrize("retired", [True, False])
def test_lifecycle_verb_toward_where_the_routine_stands_writes_nothing(retired: bool) -> None:
    repo = _FakeRoutineRepo()
    lifecycle = RoutineLifecycle(routines=_as_write_routines(repo), clock=FixedClock(instant=_T0))
    verb = lifecycle.retire if retired else lifecycle.enable

    verb(_routine(retired=retired), _CTX, by="operator")

    assert repo.recorded == []
    assert repo.changes == []
