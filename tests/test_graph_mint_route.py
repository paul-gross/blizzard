"""Graph mint route — validate, reify, warn (D-071) (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.graphs import default_graph_yaml
from tests.support import build_hub

pytestmark = pytest.mark.component

_VALID_GRAPH = """
name: tiny
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

_INVALID_GRAPH = """
name: broken
entry: missing
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: ok
          to: nowhere
"""


def test_mint_valid_graph_returns_reified_view(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _VALID_GRAPH})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "tiny"
    assert body["enabled"] is True
    assert body["entry_node_id"].startswith("nd_")
    assert {n["name"] for n in body["nodes"]} == {"build"}
    assert body["nodes"][0]["executor"] == "runner"


def test_mint_invalid_graph_is_422_with_report(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _INVALID_GRAPH})
    assert resp.status_code == 422
    report = resp.json()
    assert report["ok"] is False
    assert any("entry" in e for e in report["errors"])
    assert any("nowhere" in e for e in report["errors"])


def test_mint_malformed_yaml_is_422(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs", json={"definition_yaml": "not: a: graph: ["})
    assert resp.status_code == 422
    assert resp.json()["ok"] is False


def test_mint_default_graph_yaml_validates_clean(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs", json={"definition_yaml": default_graph_yaml()})
    # The packaged default-graph YAML (build -> deliver) parses and validates cleanly.
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "default-delivery"


_PRODUCES_GRAPH = """
name: produces-graph
entry: review
nodes:
  review:
    executor: runner
    prompt: review with cold eyes
    session: fresh
    produces:
      - review-findings
    checks:
      - pytest -q
      - ruff check
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
        fail:
          description: it does not
          to: review
    retries:
      max: 1
      exhausted: escalate
"""


def test_mint_round_trips_node_produces_and_checks_through_the_store(tmp_path: Path) -> None:
    """A node's ``produces``/``checks`` survive a store reload (D-026/D-077).

    The graph store must persist and reify these: a review node reloaded from the store
    with an empty ``produces`` never emits its ``review-findings`` asset, so the review
    fail has nothing to carry back into build — the real-rails gap behind the missing
    findings. This pins the round trip at the component tier so it can't regress
    silently (the e2e proves the asset actually lands)."""
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _PRODUCES_GRAPH})
    assert minted.status_code == 201, minted.text
    graph_id = minted.json()["graph_id"]

    # Reload from the store (not the in-memory mint) and assert the node carries both lists.
    reloaded = hub.services.graphs.get(graph_id)
    assert reloaded is not None
    review = next(n for n in reloaded.nodes if n.name == "review")
    assert review.produces == ["review-findings"]
    assert review.checks == ["pytest -q", "ruff check"]
