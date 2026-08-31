"""``RunService`` against a real store (blizzard#392, component tier) — ``build_hub``'s
own full wiring, exercised through ``HubServices.routine_run`` directly (no HTTP route
yet; Phase 2 adds one). Covers full, delta with a baseline, delta downgraded, a new
scope minted by the run, the routine's defaults reaching the chunk, the graph pin, and
the promote — the acceptance list the plan's Phase 1 owes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.routine_run import RoutineNotFoundError, ScopeRetiredError
from blizzard.hub.domain.routines import Routine, RoutineGraphUnresolvedError, RunMode
from blizzard.hub.domain.scopes import ScopeSlug
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingSetStore
from tests.support import HubHarness, build_hub, hub_store_connections

pytestmark = pytest.mark.component

_AUTHOR = WorkItemAuthor.user("usr_1")


def _default_graph(hub: HubHarness) -> Graph:
    return hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )


def _routine(
    hub: HubHarness,
    *,
    name: str = "gardening",
    scope: str = "blizzard",
    model: list[str] | None = None,
    effort: str | None = None,
) -> tuple[Routine, Graph]:
    graph = _default_graph(hub)
    routine = hub.services.routine_authoring.create(
        name=name,
        graph_name=graph.name,
        default_scope_slug=ScopeSlug.parse(scope),
        default_model=model,
        default_effort=effort,
    )
    return routine, graph


def test_full_mode_mints_ingests_and_promotes(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine, _graph = _routine(hub)

    result = hub.services.routine_run.run(routine.name, scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)

    assert result.effective_mode is RunMode.FULL
    assert result.downgraded is False
    assert result.item.routine_name == "gardening"
    assert result.item.scope_slug == "blizzard"
    assert result.item.run_mode == "full"
    facts = hub.services.chunks.load_facts(result.chunk_id)
    assert facts is not None
    assert facts.status() == ChunkStatus.READY


def test_chunk_is_pinned_to_the_routines_graph(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine, graph = _routine(hub)

    result = hub.services.routine_run.run(routine.name, scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)

    minted = hub.services.chunks.get(result.chunk_id)
    assert minted is not None
    assert minted.graph_id == graph.graph_id


def test_the_routines_model_and_effort_defaults_reach_the_minted_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine, _graph = _routine(hub, model=["opus"], effort="high")

    result = hub.services.routine_run.run(routine.name, scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)

    minted = hub.services.chunks.get(result.chunk_id)
    assert minted is not None
    assert minted.default_model == ["opus"]
    assert minted.default_effort == "high"


def test_a_scope_override_naming_no_existing_slug_mints_it_in_the_same_act(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine, _graph = _routine(hub)
    assert hub.services.scopes.get("new-scope") is None

    result = hub.services.routine_run.run(
        routine.name, scope_slug=ScopeSlug.parse("new-scope"), mode=RunMode.FULL, note=None, author=_AUTHOR
    )

    assert hub.services.scopes.get("new-scope") is not None
    assert result.item.scope_slug == "new-scope"


def test_delta_against_a_never_swept_pair_downgrades_to_full(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine, _graph = _routine(hub)

    result = hub.services.routine_run.run(routine.name, scope_slug=None, mode=RunMode.DELTA, note=None, author=_AUTHOR)

    assert result.effective_mode is RunMode.FULL
    assert result.downgraded is True
    assert result.item.run_mode == "full"
    assert "downgraded from delta" in result.item.body


def test_delta_with_a_recorded_baseline_stays_delta_and_names_its_revisions(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine, graph = _routine(hub)
    _seed_baseline(hub, graph_id=graph.graph_id, routine_name=routine.name, scope_slug="blizzard")

    result = hub.services.routine_run.run(routine.name, scope_slug=None, mode=RunMode.DELTA, note=None, author=_AUTHOR)

    assert result.effective_mode is RunMode.DELTA
    assert result.downgraded is False
    assert result.baseline is not None
    assert result.baseline.revisions == {"blizzard": "a1b2c3d"}
    assert "a1b2c3d" in result.item.body


def test_a_note_lands_in_the_charge_as_a_this_run_section(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine, _graph = _routine(hub)

    result = hub.services.routine_run.run(
        routine.name, scope_slug=None, mode=RunMode.FULL, note="focus on the auth module", author=_AUTHOR
    )

    assert "This run" in result.item.body
    assert "focus on the auth module" in result.item.body


def test_unknown_routine_name_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    with pytest.raises(RoutineNotFoundError):
        hub.services.routine_run.run("ghost", scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)


def test_a_retired_scope_is_refused_rather_than_defaulted(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine, _graph = _routine(hub)
    scope = hub.services.scopes.get("blizzard")
    assert scope is not None
    hub.services.scope_lifecycle.retire(scope, by="operator")

    with pytest.raises(ScopeRetiredError):
        hub.services.routine_run.run(routine.name, scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)


def test_a_routine_whose_graph_has_no_enabled_mint_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    routine, graph = _routine(hub)
    hub.services.graph_lifecycle.retire(graph, by="operator")

    with pytest.raises(RoutineGraphUnresolvedError):
        hub.services.routine_run.run(routine.name, scope_slug=None, mode=RunMode.FULL, note=None, author=_AUTHOR)


def _seed_baseline(hub: HubHarness, *, graph_id: str, routine_name: str, scope_slug: str) -> None:
    """A prior run's own delivered finding set — a chunk + artifact row plus the
    ``finding_sets`` row itself, seeded directly (there is no write route yet)."""
    now = datetime(2026, 7, 1, tzinfo=UTC)
    with hub.engine.begin() as conn:
        conn.execute(s.chunks.insert().values(chunk_id="ch_prior", graph_id=graph_id, minted_at=now))
        conn.execute(
            s.artifacts.insert().values(
                artifact_id="art_prior",
                chunk_id="ch_prior",
                node_id="nd_1",
                node_name="survey",
                epoch=1,
                name="findings",
                kind="asset",
                data="[]",
                produced_at=now,
            )
        )
    FindingSetStore(hub_store_connections(hub.engine)).create(
        "fins_prior",
        artifact_id="art_prior",
        chunk_id="ch_prior",
        scope_slug=scope_slug,
        routine_name=routine_name,
        revisions={"blizzard": "a1b2c3d"},
        measurement=None,
    )
