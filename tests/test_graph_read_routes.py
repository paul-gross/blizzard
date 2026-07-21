"""Graph read routes — ``GET /api/graphs`` and ``GET /api/graphs/{id}`` (component tier)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component

_GRAPH_A = """
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
        fail:
          description: it does not
          to: build
    retries:
      max: 1
      exhausted: escalate
"""

_GRAPH_B = """
name: beta
entry: ship
nodes:
  ship:
    executor: hub
    run:
      - command: "true"
    judgement:
      choices:
        landed:
          description: every repo merged cleanly
          to: done
        conflict:
          description: a repo did not merge cleanly
          to: ship
"""


_GRAPH_SESSION_SOURCE = """
name: gamma
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    session: resume:build
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: review
  review:
    executor: runner
    prompt: review it
    session: fresh
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
        fail:
          description: it does not
          to: build
"""


def _mint(hub, definition_yaml: str) -> str:
    resp = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert resp.status_code == 201, resp.text
    return resp.json()["graph_id"]


def test_list_graphs_on_empty_store_is_empty(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/graphs")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_list_graphs_marks_newest_per_name_effective(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    old_id = _mint(hub, _GRAPH_A)
    hub.clock.advance(timedelta(hours=1))
    new_id = _mint(hub, _GRAPH_A)
    other_id = _mint(hub, _GRAPH_B)

    resp = hub.client.get("/api/graphs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_id = {row["graph_id"]: row for row in body}

    assert by_id[old_id]["effective"] is False
    assert by_id[new_id]["effective"] is True
    assert by_id[other_id]["effective"] is True
    assert by_id[new_id]["name"] == "alpha"
    assert by_id[new_id]["entry_node_id"]
    assert by_id[new_id]["created_at"]

    # Newest-first ordering — the client groups by name and renders lineage
    # newest-first without re-deriving the rule.
    created_ats = [row["created_at"] for row in body]
    assert created_ats == sorted(created_ats, reverse=True)


def test_get_graph_returns_full_view(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_id = _mint(hub, _GRAPH_A)

    resp = hub.client.get(f"/api/graphs/{graph_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["graph_id"] == graph_id
    assert body["name"] == "alpha"
    assert body["enabled"] is True
    build = next(n for n in body["nodes"] if n["name"] == "build")
    assert build["executor"] == "runner"
    assert build["session"] == "resume"
    assert build["session_source"] is None
    assert build["judged_by"] == "worker"
    assert build["retries_max"] == 1
    assert build["retries_exhausted"] == "escalate"
    assert build["judgement_prompt"] == "judge it"
    assert {c["name"] for c in build["choices"]} == {"pass", "fail"}
    assert len(body["edges"]) == 2
    edge = body["edges"][0]
    assert set(edge.keys()) >= {"from_node_id", "choice_id", "to_node_name", "prompt_addendum"}


def test_get_graph_round_trips_session_source(tmp_path: Path) -> None:
    """A node's targeted ``session: resume:<name>`` form survives store persistence
    and the API node view; a bare ``resume``/``fresh`` node round-trips
    ``session_source == None`` (issue #115, Slice 2)."""
    hub = build_hub(tmp_path)
    graph_id = _mint(hub, _GRAPH_SESSION_SOURCE)

    resp = hub.client.get(f"/api/graphs/{graph_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_name = {n["name"]: n for n in body["nodes"]}

    assert by_name["build"]["session"] == "resume"
    assert by_name["build"]["session_source"] == "build"

    assert by_name["review"]["session"] == "fresh"
    assert by_name["review"]["session_source"] is None


def test_get_graph_unknown_id_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/graphs/gr_does_not_exist")
    assert resp.status_code == 404
    assert "gr_does_not_exist" in resp.json()["detail"]
