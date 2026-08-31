"""``RunService``/``compose_charge`` (unit tier, blizzard#392): mint, ingest, and
promote a hub work item from a routine over fake repositories — only the members each
method actually touches are live (``bzh:domain-core``, the ``test_routine_domain.py``
isolation shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.findings import FindingSet, IReadFindingSetRepository
from blizzard.hub.domain.graph import Graph, IReadGraphRepository
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.hub.domain.routine_run import RoutineNotFoundError, RunService, ScopeRetiredError, compose_charge
from blizzard.hub.domain.routines import IReadRoutineRepository, Routine, RoutineGraphUnresolvedError, RunMode
from blizzard.hub.domain.scopes import IReadScopeRepository, IWriteScopeRepository, Scope, ScopeRegistry, ScopeSlug
from blizzard.hub.domain.work import (
    Chunk,
    IReadChunkRepository,
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkItemRecord,
    WorkRef,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_GRAPH = Graph(graph_id="gr_1", name="default", entry_node_id="nd_1", nodes=[], edges=[], created_at=_T0)
_ROUTINE = Routine(
    routine_id="rtn_1",
    name="gardening",
    graph_name="default",
    default_scope_slug="blizzard",
    created_at=_T0,
    default_model=["opus"],
    default_effort="high",
)


@dataclass
class _FakeRoutines:
    by_name: dict[str, Routine] = field(default_factory=lambda: {"gardening": _ROUTINE})

    def get_by_name(self, name: str) -> Routine | None:
        return self.by_name.get(name)

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


@dataclass
class _FakeGraphs:
    resolvable: dict[str, Graph] = field(default_factory=lambda: {"default": _GRAPH})

    def get_enabled_by_name(self, name: str) -> Graph | None:
        return self.resolvable.get(name)

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


@dataclass
class _FakeScopes:
    scopes: dict[str, Scope] = field(
        default_factory=lambda: {"blizzard": Scope(slug="blizzard", description="the hub itself", created_at=_T0)}
    )
    retired: set[str] = field(default_factory=set)
    ensured: list[str] = field(default_factory=list)

    def get(self, slug: str) -> Scope | None:
        return self.scopes.get(slug)

    def list_all(self) -> list[Scope]:
        return list(self.scopes.values())

    def is_retired(self, slug: str) -> bool:
        return slug in self.retired

    def ensure(self, slug: str, *, description: str, at: datetime) -> Scope:
        self.ensured.append(slug)
        existing = self.scopes.get(slug)
        if existing is not None:
            return existing
        scope = Scope(slug=slug, description=description, created_at=at)
        self.scopes[slug] = scope
        return scope

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


@dataclass
class _FakeFindingSets:
    newest: dict[tuple[str, str], FindingSet] = field(default_factory=dict)

    def newest_for_routine_scope(self, routine_name: str, scope_slug: str) -> FindingSet | None:
        return self.newest.get((routine_name, scope_slug))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


@dataclass
class _FakeChunks:
    ready: list[Chunk] = field(default_factory=list)
    positions: dict[str, float] = field(default_factory=dict)
    promoted_ats_by_chunk: dict[str, datetime] = field(default_factory=dict)
    live_holder: str | None = None

    def list_ready(self) -> list[Chunk]:
        return self.ready

    def queue_positions(self) -> dict[str, float]:
        return self.positions

    def promoted_ats(self) -> dict[str, datetime]:
        return self.promoted_ats_by_chunk

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        return self.live_holder

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


@dataclass
class _FakeItems:
    next_ref: int = 1
    calls: list[dict[str, Any]] = field(default_factory=list)

    def allocate_ref(self, source: str) -> str:
        ref = str(self.next_ref)
        self.next_ref += 1
        return ref

    def create_with_chunk_and_promote(self, **kwargs: Any) -> tuple[WorkItemRecord, int | None]:
        self.calls.append(kwargs)
        record = WorkItemRecord(
            work_item_id="wi_1",
            source=kwargs["pointer"].source,
            ref=kwargs["pointer"].ref,
            title=kwargs["title"],
            body=kwargs["body"],
            author=kwargs["author"],
            stated_priority=None,
            created_at=kwargs["at"],
            edited_at=kwargs["at"],
            routine_name=kwargs["routine_name"],
            scope_slug=kwargs["scope_slug"],
            run_mode=kwargs["run_mode"],
        )
        return record, 42

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def _service(
    *,
    routines: _FakeRoutines | None = None,
    scopes: _FakeScopes | None = None,
    graphs: _FakeGraphs | None = None,
    finding_sets: _FakeFindingSets | None = None,
    items: _FakeItems | None = None,
    chunks: _FakeChunks | None = None,
) -> tuple[RunService, _FakeItems, _FakeScopes, _FakeChunks]:
    clock = FixedClock(instant=_T0)
    routines = routines or _FakeRoutines()
    scopes = scopes or _FakeScopes()
    graphs = graphs or _FakeGraphs()
    finding_sets = finding_sets or _FakeFindingSets()
    items = items or _FakeItems()
    chunks = chunks or _FakeChunks()
    service = RunService(
        routines=cast(IReadRoutineRepository, routines),
        scopes=cast(IReadScopeRepository, scopes),
        scope_registry=ScopeRegistry(scopes=cast(IWriteScopeRepository, scopes), clock=clock),
        graphs=cast(IReadGraphRepository, graphs),
        finding_sets=cast(IReadFindingSetRepository, finding_sets),
        items=cast(IWriteWorkItemRepository, items),
        chunks=cast(IReadChunkRepository, chunks),
        clock=clock,
    )
    return service, items, scopes, chunks


_AUTHOR = WorkItemAuthor.user("usr_1")


def test_run_unknown_routine_raises() -> None:
    service, *_ = _service()

    with pytest.raises(RoutineNotFoundError, match="ghost"):
        service.run("ghost", scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)


def test_run_unresolved_graph_raises_naming_it() -> None:
    service, *_ = _service(graphs=_FakeGraphs(resolvable={}))

    with pytest.raises(RoutineGraphUnresolvedError, match="default"):
        service.run("gardening", scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)


def test_run_retired_default_scope_is_refused() -> None:
    service, *_ = _service(scopes=_FakeScopes(retired={"blizzard"}))

    with pytest.raises(ScopeRetiredError, match="blizzard"):
        service.run("gardening", scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)


def test_run_retired_override_scope_is_refused() -> None:
    scopes = _FakeScopes(
        scopes={
            "blizzard": Scope(slug="blizzard", description="", created_at=_T0),
            "cold": Scope(slug="cold", description="", created_at=_T0),
        },
        retired={"cold"},
    )
    service, *_ = _service(scopes=scopes)

    with pytest.raises(ScopeRetiredError, match="cold"):
        service.run("gardening", scope_slug=ScopeSlug.parse("cold"), mode=RunMode.FULL, note=None, author=_AUTHOR)


def test_run_override_scope_mints_an_unseen_slug() -> None:
    service, items, scopes, _chunks = _service()

    service.run("gardening", scope_slug=ScopeSlug.parse("new-scope"), mode=RunMode.FULL, note=None, author=_AUTHOR)

    assert "new-scope" in scopes.ensured
    assert items.calls[0]["scope_slug"] == "new-scope"


def test_run_delta_with_no_baseline_downgrades_to_full() -> None:
    service, items, *_ = _service()

    result = service.run("gardening", scope_slug=None, mode=RunMode.DELTA, note=None, author=_AUTHOR)

    assert result.effective_mode is RunMode.FULL
    assert result.downgraded is True
    assert items.calls[0]["run_mode"] == "full"


def test_run_delta_with_a_recorded_baseline_stays_delta() -> None:
    baseline = FindingSet(
        finding_set_id="fins_1",
        artifact_id="art_1",
        chunk_id="ch_prior",
        scope_slug="blizzard",
        routine_name="gardening",
        revisions={"blizzard": "a1b2c3d"},
        measurement=None,
    )
    service, items, *_ = _service(finding_sets=_FakeFindingSets(newest={("gardening", "blizzard"): baseline}))

    result = service.run("gardening", scope_slug=None, mode=RunMode.DELTA, note=None, author=_AUTHOR)

    assert result.effective_mode is RunMode.DELTA
    assert result.downgraded is False
    assert result.baseline == baseline
    assert items.calls[0]["run_mode"] == "delta"


def test_run_full_mode_never_downgrades_even_with_no_baseline() -> None:
    service, *_ = _service()

    result = service.run("gardening", scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)

    assert result.effective_mode is RunMode.FULL
    assert result.downgraded is False


def test_run_threads_the_routines_model_and_effort_defaults_onto_the_chunk() -> None:
    service, items, *_ = _service()

    service.run("gardening", scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)

    chunk = items.calls[0]["chunk"]
    assert chunk.default_model == ["opus"]
    assert chunk.default_effort == "high"


def test_run_computes_the_tail_position_before_the_write() -> None:
    chunks = _FakeChunks(
        ready=[Chunk(chunk_id="ch_a", graph_id="gr_1", work_refs=[], minted_at=_T0)], positions={"ch_a": 3.0}
    )
    service, items, *_ = _service(chunks=chunks)

    service.run("gardening", scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)

    assert items.calls[0]["position"] == 4.0


def test_run_raises_ingest_conflict_when_the_freshly_allocated_ref_races_a_live_holder() -> None:
    service, *_ = _service(chunks=_FakeChunks(live_holder="ch_other"))

    with pytest.raises(IngestConflict):
        service.run("gardening", scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)


def test_run_uses_the_injected_clock_not_the_wall_clock() -> None:
    service, items, *_ = _service()

    service.run("gardening", scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)

    assert items.calls[0]["at"] == _T0


# --- compose_charge (a pure function, no store) -----------------------------------


def test_compose_charge_names_the_routine_graph_and_scope() -> None:
    charge = compose_charge(
        routine_name="gardening",
        graph_name="default",
        scope_slug="blizzard",
        scope_description="the hub itself",
        mode=RunMode.FULL,
        downgraded=False,
        baseline=None,
        note=None,
    )
    assert "Routine: gardening (graph: default)" in charge
    assert "Scope: blizzard — the hub itself" in charge
    assert "Mode: full" in charge


def test_compose_charge_names_the_downgrade() -> None:
    charge = compose_charge(
        routine_name="gardening",
        graph_name="default",
        scope_slug="blizzard",
        scope_description="",
        mode=RunMode.FULL,
        downgraded=True,
        baseline=None,
        note=None,
    )
    assert "downgraded from delta" in charge


def test_compose_charge_names_the_baseline_revisions() -> None:
    baseline = FindingSet(
        finding_set_id="fins_1",
        artifact_id="art_1",
        chunk_id="ch_prior",
        scope_slug="blizzard",
        routine_name="gardening",
        revisions={"blizzard": "a1b2c3d"},
        measurement=None,
    )
    charge = compose_charge(
        routine_name="gardening",
        graph_name="default",
        scope_slug="blizzard",
        scope_description="",
        mode=RunMode.DELTA,
        downgraded=False,
        baseline=baseline,
        note=None,
    )
    assert "fins_1" in charge
    assert "blizzard@a1b2c3d" in charge


def test_compose_charge_appends_the_note_as_a_this_run_section() -> None:
    charge = compose_charge(
        routine_name="gardening",
        graph_name="default",
        scope_slug="blizzard",
        scope_description="",
        mode=RunMode.FULL,
        downgraded=False,
        baseline=None,
        note="focus on the auth module",
    )
    assert "This run" in charge
    assert "focus on the auth module" in charge


def test_compose_charge_omits_the_this_run_section_with_no_note() -> None:
    charge = compose_charge(
        routine_name="gardening",
        graph_name="default",
        scope_slug="blizzard",
        scope_description="",
        mode=RunMode.FULL,
        downgraded=False,
        baseline=None,
        note=None,
    )
    assert "This run" not in charge
