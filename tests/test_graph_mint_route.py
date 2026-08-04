"""Graph mint route — validate, reify, warn (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import ProducesSpec
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
      - name: commit
        kind: git_commit
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
    """A node's ``produces``/``checks`` survive a store reload — both authored forms
    (D1, issue #143): the bare-string ``review-findings`` (``kind=asset``, every
    pre-#143 graph's shape) and the mapping ``{name: commit, kind: git_commit}``."""
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _PRODUCES_GRAPH})
    assert minted.status_code == 201, minted.text
    graph_id = minted.json()["graph_id"]

    # Reload from the store (not the in-memory mint) and assert the node carries both lists.
    reloaded = hub.services.graphs.get(graph_id)
    assert reloaded is not None
    review = next(n for n in reloaded.nodes if n.name == "review")
    assert review.produces == [
        ProducesSpec(name="review-findings", kind=ArtifactKind.ASSET),
        ProducesSpec(name="commit", kind=ArtifactKind.GIT_COMMIT),
    ]
    assert review.checks == ["pytest -q", "ruff check"]


_BOUNCE_CAP_GRAPH = """
name: bounce-cap-graph
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
          to: deliver
        fail:
          description: it does not
          to: build
    retries:
      max: 1
      exhausted: escalate
  deliver:
    executor: hub
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
    bounce_cap: 3
"""


def test_mint_round_trips_bounce_cap_through_the_store(tmp_path: Path) -> None:
    """A hub node's authored ``bounce_cap`` (#64) survives a store reload."""
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _BOUNCE_CAP_GRAPH})
    assert minted.status_code == 201, minted.text
    graph_id = minted.json()["graph_id"]

    reloaded = hub.services.graphs.get(graph_id)
    assert reloaded is not None
    deliver = next(n for n in reloaded.nodes if n.name == "deliver")
    assert deliver.bounce_cap == 3


_POLL_CADENCE_GRAPH = """
name: poll-cadence-graph
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
          to: merge
        fail:
          description: it does not
          to: build
    retries:
      max: 1
      exhausted: escalate
  merge:
    executor: hub
    run:
      - command: check-ci
    poll_interval: 15
    poll_timeout: 600
    judgement:
      choices:
        success:
          description: green
          to: done
        failure:
          description: red
          to: build
"""


def test_mint_round_trips_poll_interval_and_timeout_through_the_store(tmp_path: Path) -> None:
    """A hub command node's ``poll_interval``/``poll_timeout`` (#66) survive a store reload."""
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _POLL_CADENCE_GRAPH})
    assert minted.status_code == 201, minted.text
    graph_id = minted.json()["graph_id"]

    reloaded = hub.services.graphs.get(graph_id)
    assert reloaded is not None
    merge = next(n for n in reloaded.nodes if n.name == "merge")
    assert merge.poll_interval_seconds == 15
    assert merge.poll_timeout_seconds == 600
