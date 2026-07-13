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
