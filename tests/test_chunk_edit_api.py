"""The ``/chunks/{id}/graph`` and ``/chunks/{id}/model`` routes over the HTTP surface (issue #27).

A not-ready chunk's workflow graph and model selection are editable through these two
routes; both are refused (409) once the chunk has left ``not_ready`` (promoted, claimed,
running, or later). The refusal itself (``EditService``) is unit-tested in
``test_edit_service.py``; this file proves the controller wires it correctly end to end —
the read side (``graph_id``/``model`` on the list/detail views), the write, the 404s, and
the ``chunk-changed`` event.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.work import DEFAULT_MODEL
from tests.support import build_hub, emitted_events, ingest

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "12"}

_ALT_YAML = """
name: alt-graph
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: deliver
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    mode: merge-to-main
"""


def _mint_alt_graph(hub) -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _ALT_YAML})
    assert resp.status_code == 201, resp.text
    return resp.json()["graph_id"]


# --------------------------------------------------------------------------- #
# Read — graph_id/model already ride the list/detail views.
# --------------------------------------------------------------------------- #


def test_a_freshly_ingested_chunk_carries_the_default_graph_and_model(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "not_ready"
    assert detail["model"] == DEFAULT_MODEL
    default_graph_id = detail["graph_id"]

    summary = next(c for c in hub.client.get("/api/chunks").json() if c["chunk_id"] == chunk_id)
    assert summary["model"] == DEFAULT_MODEL
    assert summary["graph_id"] == default_graph_id


# --------------------------------------------------------------------------- #
# Write — graph edit.
# --------------------------------------------------------------------------- #


def test_edit_graph_returns_202_and_the_detail_carries_the_new_graph(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    alt_graph_id = _mint_alt_graph(hub)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/graph", json={"graph_id": alt_graph_id})

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"chunk_id": chunk_id, "graph_id": alt_graph_id}
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == alt_graph_id
    assert detail["status"] == "not_ready"


def test_edit_graph_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    alt_graph_id = _mint_alt_graph(hub)

    resp = hub.client.post("/api/chunks/ch_nope/graph", json={"graph_id": alt_graph_id})

    assert resp.status_code == 404


def test_edit_graph_unknown_graph_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/graph", json={"graph_id": "gr_nope"})

    assert resp.status_code == 404
    original = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]
    assert original != "gr_nope"


def test_edit_graph_refuses_once_the_chunk_is_ready(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])  # promote=True by default
    alt_graph_id = _mint_alt_graph(hub)
    before = hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"]

    resp = hub.client.post(f"/api/chunks/{chunk_id}/graph", json={"graph_id": alt_graph_id})

    assert resp.status_code == 409, resp.text
    assert "ready" in resp.json()["detail"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == before


def test_edit_graph_refuses_once_the_chunk_is_claimed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    alt_graph_id = _mint_alt_graph(hub)
    claim = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert claim.status_code == 201, claim.text

    resp = hub.client.post(f"/api/chunks/{chunk_id}/graph", json={"graph_id": alt_graph_id})

    assert resp.status_code == 409, resp.text
    assert "running" in resp.json()["detail"]


def test_edit_graph_publishes_chunk_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    alt_graph_id = _mint_alt_graph(hub)
    since = hub.events.latest_id()

    resp = hub.client.post(f"/api/chunks/{chunk_id}/graph", json={"graph_id": alt_graph_id})

    assert resp.status_code == 202, resp.text
    events = emitted_events(hub, since=since)
    types = [e["event"] for e in events]
    assert "chunk-changed" in types


# --------------------------------------------------------------------------- #
# Write — model edit.
# --------------------------------------------------------------------------- #


def test_edit_model_returns_202_and_the_detail_carries_the_new_model(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/model", json={"model": "claude-sonnet-4-5"})

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"chunk_id": chunk_id, "model": "claude-sonnet-4-5"}
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["model"] == "claude-sonnet-4-5"
    assert detail["status"] == "not_ready"


def test_edit_model_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/chunks/ch_nope/model", json={"model": "claude-sonnet-4-5"})
    assert resp.status_code == 404


def test_edit_model_blank_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/model", json={"model": "   "})

    assert resp.status_code == 422, resp.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["model"] == DEFAULT_MODEL


def test_edit_model_refuses_once_the_chunk_is_ready(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])  # promote=True by default

    resp = hub.client.post(f"/api/chunks/{chunk_id}/model", json={"model": "claude-sonnet-4-5"})

    assert resp.status_code == 409, resp.text
    assert "ready" in resp.json()["detail"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["model"] == DEFAULT_MODEL


def test_edit_model_refuses_once_the_chunk_is_claimed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER])
    claim = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert claim.status_code == 201, claim.text

    resp = hub.client.post(f"/api/chunks/{chunk_id}/model", json={"model": "claude-sonnet-4-5"})

    assert resp.status_code == 409, resp.text
    assert "running" in resp.json()["detail"]


def test_edit_model_publishes_chunk_changed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_POINTER], promote=False)
    since = hub.events.latest_id()

    resp = hub.client.post(f"/api/chunks/{chunk_id}/model", json={"model": "claude-sonnet-4-5"})

    assert resp.status_code == 202, resp.text
    events = emitted_events(hub, since=since)
    types = [e["event"] for e in events]
    assert "chunk-changed" in types
