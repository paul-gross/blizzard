"""A cadence's errands, their counts, and the cadence a run names (component tier): the errands route reads each
errand's state, last run, and next due instant against the hub's own clock; the cadence views count errands by
state; a run's views name the cadence and chore that minted it — nothing for a hand run, no key for a removed chore —
and the cadence and run lists stay flat in statements as they grow."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.cadence.errands import errands
from blizzard.hub.domain.cadence.services import CadenceAttention
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.scopes import ScopeSlug
from tests.support.scaffolding import HubHarness, build_hub, count_queries, default_project, project_record

_CTX = ChangeContext(actor="operator", door=Door.API)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _routine(hub: HubHarness, slug: str, *linked: str) -> Routine:
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    project = project_record(hub, default_project(hub.engine))
    routine = hub.services.routine_authoring.create(
        project=project, ctx=_CTX, slug=slug, graph_name=graph.name, default_scope_slug=ScopeSlug.parse(slug)
    )
    for scope_slug in linked:
        scope = hub.services.scope_registry.ensure(project, ScopeSlug.parse(scope_slug), _CTX)
        routine = hub.services.routine_scope_membership.link(routine, scope, _CTX)
    return routine


def _create(hub: HubHarness, slug: str = "nightly", **fields: Any) -> dict[str, Any]:
    body = {"slug": slug, "spacing": "1h", **fields}
    resp = hub.client.post(f"/api/projects/{default_project(hub.engine)}/cadences", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _errands(hub: HubHarness, cadence_id: str) -> list[dict[str, Any]]:
    resp = hub.client.get(f"/api/cadences/{cadence_id}/errands")
    assert resp.status_code == 200, resp.text
    return list(resp.json())


def _mint_all(hub: HubHarness, cadence_id: str) -> list[str]:
    """Mint every errand of the cadence as the sweep would, returning the minted chunk ids."""
    cadence = hub.services.cadences.get(cadence_id)
    assert cadence is not None
    attention = CadenceAttention(
        routines=hub.services.routines,
        routine_scopes=hub.services.routine_scopes,
        scopes=hub.services.scopes,
        graphs=hub.services.graphs,
    )
    facts = attention.facts([cadence])[cadence.project_id]
    minted: list[str] = []
    for errand in errands(cadence, facts):
        inputs = hub.services.errand_planning.inputs([(errand, facts)])
        result = hub.services.cadence_mint.mint(errand, judged=None, inputs=inputs[errand.key])
        assert result is not None
        minted.append(result.chunk.chunk_id)
    return minted


def test_a_never_run_errand_waits_and_is_due_at_the_instant_it_joined(hub: HubHarness) -> None:
    _routine(hub, "sweep")
    joined = hub.clock.now()
    cadence = _create(hub, chores=[{"key": "plain", "routine": "sweep"}])
    hub.clock.advance(timedelta(minutes=5))

    [row] = _errands(hub, cadence["cadence_id"])

    assert (row["chore"], row["scope"], row["state"], row["attention"], row["last_run"]) == (
        "plain",
        "sweep",
        "due",
        [],
        None,
    )
    assert row["due_at"].startswith(joined.strftime("%Y-%m-%dT%H:%M:%S"))


def test_a_minted_errand_reads_running_with_its_run_then_waits_for_the_next_due_instant(hub: HubHarness) -> None:
    _routine(hub, "sweep")
    cadence = _create(hub, chores=[{"key": "plain", "routine": "sweep"}])
    [chunk_id] = _mint_all(hub, cadence["cadence_id"])

    [running] = _errands(hub, cadence["cadence_id"])

    assert running["state"] == "running"
    assert running["due_at"] is None
    assert running["last_run"]["chunk_id"] == chunk_id
    assert running["last_run"]["finished_at"] is None
    assert running["last_run"]["scheduled_at"]


def test_the_errands_go_chore_by_chore_and_by_scope_within_a_chore(hub: HubHarness) -> None:
    _routine(hub, "sweep", "zeta", "alpha")
    cadence = _create(
        hub,
        chores=[
            {"key": "second", "routine": "sweep", "scopes": ["zeta", "alpha"]},
            {"key": "first", "routine": "sweep"},
        ],
    )

    rows = _errands(hub, cadence["cadence_id"])

    assert [(r["chore"], r["scope"]) for r in rows] == [("second", "alpha"), ("second", "zeta"), ("first", "sweep")]


def test_attention_stops_its_errand_and_the_view_counts_it(hub: HubHarness) -> None:
    _routine(hub, "sweep")
    cadence = _create(hub, chores=[{"key": "ok", "routine": "sweep"}, {"key": "ghost", "routine": "nope"}])

    rows = _errands(hub, cadence["cadence_id"])
    body = hub.client.get(f"/api/cadences/{cadence['cadence_id']}").json()

    assert [(r["chore"], r["state"]) for r in rows] == [("ok", "due")]
    assert body["errand_counts"] == {"due": 1, "running": 0, "waiting": 0, "stopped": 0}


def test_a_scope_attention_stops_one_errand_with_its_reason(hub: HubHarness) -> None:
    _routine(hub, "sweep")
    cadence = _create(hub, chores=[{"key": "listed", "routine": "sweep", "scopes": ["sweep", "unlinked"]}])
    project = project_record(hub, default_project(hub.engine))
    hub.services.scope_registry.ensure(project, ScopeSlug.parse("unlinked"), _CTX)

    rows = _errands(hub, cadence["cadence_id"])

    stopped = {r["scope"]: r for r in rows}["unlinked"]
    assert stopped["state"] == "stopped"
    assert stopped["due_at"] is None
    assert [a["reason"] for a in stopped["attention"]] == ["scope_unlinked"]


def test_a_retired_cadence_stops_every_errand_while_a_live_run_still_shows(hub: HubHarness) -> None:
    _routine(hub, "sweep")
    cadence = _create(hub, chores=[{"key": "plain", "routine": "sweep"}])
    [chunk_id] = _mint_all(hub, cadence["cadence_id"])
    retired = hub.client.post(f"/api/cadences/{cadence['cadence_id']}/retire", json={})
    assert retired.status_code == 202, retired.text

    [row] = _errands(hub, cadence["cadence_id"])

    assert (row["state"], row["attention"], row["due_at"]) == ("stopped", [], None)
    assert row["last_run"]["chunk_id"] == chunk_id
    assert retired.json()["errand_counts"] == {"due": 0, "running": 0, "waiting": 0, "stopped": 1}


def test_an_unknown_cadence_has_no_errands(hub: HubHarness) -> None:
    assert hub.client.get("/api/cadences/cad_nope/errands").status_code == 404


def test_the_list_counts_each_cadences_errands_by_state(hub: HubHarness) -> None:
    _routine(hub, "sweep", "a", "b")
    running = _create(hub, "busy", chores=[{"key": "all", "routine": "sweep", "scopes": ["a"]}])
    _mint_all(hub, running["cadence_id"])
    _create(hub, "idle", chores=[{"key": "all", "routine": "sweep", "scopes": ["a", "b"]}])

    counts = {c["slug"]: c["errand_counts"] for c in hub.client.get("/api/cadences").json()}

    assert counts["busy"] == {"due": 0, "running": 1, "waiting": 0, "stopped": 0}
    assert counts["idle"] == {"due": 2, "running": 0, "waiting": 0, "stopped": 0}


def _run_of(hub: HubHarness, chunk_id: str) -> dict[str, Any]:
    resp = hub.client.get(f"/api/runs/{chunk_id}")
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def _listed(hub: HubHarness, chunk_id: str) -> dict[str, Any]:
    hub.clock.advance(timedelta(minutes=1))  # the window is half-open: a run minted this instant is not yet in it
    [row] = [r for r in hub.client.get("/api/runs").json() if r["chunk_id"] == chunk_id]
    return dict(row)


def test_a_minted_run_names_its_cadence_and_chore_and_a_hand_run_names_none(hub: HubHarness) -> None:
    routine = _routine(hub, "sweep")
    cadence = _create(hub, "nightly", name="Nightly", chores=[{"key": "plain", "routine": "sweep"}])
    [minted] = _mint_all(hub, cadence["cadence_id"])
    scope = hub.services.scopes.get(routine.project_id, "sweep")
    assert scope is not None
    hand = hub.services.routine_run.run(
        routine, scope=scope, mode=RunMode.FULL, note=None, author=WorkItemAuthor.user("u")
    ).chunk_id
    chore_id = cadence["chores"][0]["chore_id"]

    expected = {
        "cadence_id": cadence["cadence_id"],
        "cadence_slug": "nightly",
        "cadence_name": "Nightly",
        "chore_id": chore_id,
        "chore_key": "plain",
    }
    assert _listed(hub, minted)["cadence"] == expected
    assert _run_of(hub, minted)["cadence"] == expected
    assert _listed(hub, hand)["cadence"] is None
    assert _run_of(hub, hand)["cadence"] is None


def test_a_removed_chore_leaves_its_run_naming_the_chore_by_id_alone(hub: HubHarness) -> None:
    _routine(hub, "sweep")
    cadence = _create(hub, chores=[{"key": "plain", "routine": "sweep"}, {"key": "other", "routine": "sweep"}])
    [minted, _] = _mint_all(hub, cadence["cadence_id"])
    chore_id = cadence["chores"][0]["chore_id"]
    edited = hub.client.patch(
        f"/api/cadences/{cadence['cadence_id']}", json={"chores": [{"key": "other", "routine": "sweep"}]}
    )
    assert edited.status_code == 200, edited.text

    run = _run_of(hub, minted)["cadence"]

    assert (run["chore_id"], run["chore_key"], run["cadence_slug"]) == (chore_id, None, "nightly")


def _seed(hub: HubHarness, cadences: int) -> None:
    _routine(hub, "sweep", "a", "b")
    for index in range(cadences):
        created = _create(
            hub,
            f"cadence-{index}",
            chores=[{"key": "all", "routine": "sweep", "scopes": "all"}, {"key": "ghost", "routine": "nope"}],
        )
        _mint_all(hub, created["cadence_id"])


def test_the_cadence_and_run_lists_read_in_a_flat_number_of_queries(tmp_path: Path) -> None:
    (tmp_path / "few").mkdir()
    (tmp_path / "many").mkdir()
    few, many = build_hub(tmp_path / "few"), build_hub(tmp_path / "many")
    _seed(few, 1)
    _seed(many, 4)

    def read(hub: HubHarness, path: str) -> None:
        assert hub.client.get(path).status_code == 200

    for path in ("/api/cadences", "/api/runs"):
        assert count_queries(few.engine, lambda p=path: read(few, p)) == count_queries(
            many.engine, lambda p=path: read(many, p)
        )


def test_the_errands_of_a_cadence_read_in_a_flat_number_of_queries(tmp_path: Path) -> None:
    (tmp_path / "few").mkdir()
    (tmp_path / "many").mkdir()
    few, many = build_hub(tmp_path / "few"), build_hub(tmp_path / "many")
    _routine(few, "sweep", "a")
    _routine(many, "sweep", "a", "b", "c", "d")
    ids = [_create(h, chores=[{"key": "all", "routine": "sweep", "scopes": "all"}])["cadence_id"] for h in (few, many)]

    assert count_queries(few.engine, lambda: _errands(few, ids[0])) == count_queries(
        many.engine, lambda: _errands(many, ids[1])
    )
