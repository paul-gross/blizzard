"""``POST /api/routines/{routine_id}/run`` (blizzard#392, component tier) — mints,
ingests, and promotes a hub work item from a routine over the real HTTP surface, the
``tests/test_hub_work_source_api.py`` shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.hub.events.broker import CHUNK_CHANGED, QUEUE_CHANGED
from tests.support import build_hub, emitted_events

pytestmark = pytest.mark.component

_GRAPH = """
name: alpha
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""


def _mint_graph(hub, definition_yaml: str = _GRAPH) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert resp.status_code == 201, resp.text


def _create_routine(hub, **overrides: object) -> dict:  # type: ignore[no-untyped-def, type-arg]
    body: dict[str, object] = {
        "name": "gardening",
        "graph_name": "alpha",
        "default_scope_slug": "blizzard",
        "default_model": [],
        "default_effort": None,
    }
    body.update(overrides)
    resp = hub.client.post("/api/routines", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_run_mints_ingests_and_promotes(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["effective_mode"] == "full"
    assert body["downgraded"] is False
    assert body["routine_name"] == "gardening"
    assert body["scope_slug"] == "blizzard"
    chunk = hub.client.get(f"/api/chunks/{body['chunk_id']}").json()
    assert chunk["status"] == "ready"
    assert chunk["graph_id"] == hub.client.get("/api/graphs").json()[0]["graph_id"]


def test_run_defaults_to_the_routines_own_scope(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    body = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={}).json()

    assert body["scope_slug"] == "blizzard"


def test_run_scope_override_unknown_slug_is_422_and_never_mints(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    assert hub.client.get("/api/scopes/new-scope").status_code == 404

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={"scope_slug": "new-scope"})

    assert resp.status_code == 422, resp.text
    assert "new-scope" in resp.json()["detail"]
    assert hub.client.get("/api/scopes/new-scope").status_code == 404


def test_run_scope_override_unrelated_scope_is_422_naming_the_relating_command(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    created = hub.client.post("/api/scopes", json={"slug": "unrelated", "description": ""})
    assert created.status_code == 201, created.text

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={"scope_slug": "unrelated"})

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "unrelated" in detail
    assert f"routine scope add {routine['routine_id']} unrelated" in detail


def test_run_scope_override_a_related_scope_runs_against_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    hub.client.post("/api/scopes", json={"slug": "related", "description": ""})
    linked = hub.client.put(f"/api/routines/{routine['routine_id']}/scopes/related")
    assert linked.status_code == 204, linked.text

    body = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={"scope_slug": "related"}).json()

    assert body["scope_slug"] == "related"


def test_run_delta_with_no_recorded_baseline_downgrades_to_full(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    body = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={"mode": "delta"}).json()

    assert body["effective_mode"] == "full"
    assert body["downgraded"] is True
    assert "downgraded from delta" in body["body"]


def test_run_a_note_lands_in_the_charge(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    body = hub.client.post(
        f"/api/routines/{routine['routine_id']}/run", json={"note": "focus on the auth module"}
    ).json()

    assert "This run" in body["body"]
    assert "focus on the auth module" in body["body"]


def test_run_unknown_routine_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    resp = hub.client.post("/api/routines/rtn_ghost/run", json={})

    assert resp.status_code == 404


def test_run_unknown_mode_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={"mode": "sideways"})

    assert resp.status_code == 422, resp.text
    assert "sideways" in resp.json()["detail"]


def test_run_malformed_scope_slug_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={"scope_slug": "Not A Slug"})

    assert resp.status_code == 422, resp.text
    assert "Not A Slug" in resp.json()["detail"]


def test_run_against_a_routine_whose_graph_lost_its_enabled_mint_is_503(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    graph_id = hub.client.get("/api/graphs").json()[0]["graph_id"]
    resp = hub.client.post(f"/api/graphs/{graph_id}/retire", json={})
    assert resp.status_code == 202, resp.text

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={})

    assert resp.status_code == 503, resp.text
    assert "alpha" in resp.json()["detail"]


def test_run_against_a_retired_scope_is_503(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    resp = hub.client.post("/api/scopes/blizzard/retire", json={})
    assert resp.status_code == 202, resp.text

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={})

    assert resp.status_code == 503, resp.text
    assert "blizzard" in resp.json()["detail"]


def test_run_is_409_when_an_out_of_band_ingest_already_holds_the_allocated_ref(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)
    pre_ingested = hub.client.post("/api/chunks", json={"tokens": ["hub:1"]})
    assert pre_ingested.status_code == 201, pre_ingested.text
    existing_chunk_id = pre_ingested.json()["chunk_id"]

    resp = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={})

    assert resp.status_code == 409, resp.text
    assert resp.json()["existing_chunk_id"] == existing_chunk_id


def test_run_publishes_one_minted_chunk_changed_frame_reading_ready(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    created = hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={}).json()

    frames = [json.loads(e["data"]) for e in emitted_events(hub) if e["event"] == CHUNK_CHANGED]
    assert len(frames) == 1
    assert frames[0]["chunk_id"] == created["chunk_id"]
    assert frames[0]["cause"] == "minted"
    assert frames[0]["status"] == "ready"


def test_run_also_publishes_queue_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _mint_graph(hub)
    routine = _create_routine(hub)

    hub.client.post(f"/api/routines/{routine['routine_id']}/run", json={})

    assert [e["event"] for e in emitted_events(hub)] == [CHUNK_CHANGED, QUEUE_CHANGED]
