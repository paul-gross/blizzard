"""Graph mint route — validate, reify, warn (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import GraphArtifact, ProducesSpec
from blizzard.hub.graphs import PACKAGED, GraphFile
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
    resp = hub.client.post("/api/graphs", json={"definition_yaml": PACKAGED.default.text})
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


_ARTIFACTS_GRAPH = """
name: artifacts-graph
entry: build
artifacts:
  docket: the docket's baked text
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


def test_mint_round_trips_graph_artifacts_through_the_store(tmp_path: Path) -> None:
    """A graph-scoped ``artifacts:`` entry survives a store reload, in authored order
    — the mint route never inlines a file reference of its own: the raw body
    already carries the baked content."""
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _ARTIFACTS_GRAPH})
    assert minted.status_code == 201, minted.text
    graph_id = minted.json()["graph_id"]

    reloaded = hub.services.graphs.get(graph_id)
    assert reloaded is not None
    assert reloaded.artifacts == [GraphArtifact(name="docket", content="the docket's baked text", ordinal=0)]


_FILE_ARTIFACTS_GRAPH = """
name: file-artifacts-graph
artifacts:
  docket: ./reference-notes.md
entry: build
nodes:
  build:
    executor: runner
    prompt: ./prompts/build.md
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""

# Multi-line, blank-line-bearing, newline-terminated — the shape a real declared file has,
# and the one a re-serialization could quietly reflow.
_DOCKET_TEXT = "# Docket\n\n- one term: what it means\n- another term\n\n  a continuation line\n"


def test_a_file_declared_artifact_reminted_from_inlined_yaml_bakes_the_files_own_text(tmp_path: Path) -> None:
    """The whole edge ``hub graph mint <path>`` traverses: the loader inlines each
    ``artifacts:`` file reference, ``inlined_yaml`` re-serializes it, and posting that as a
    raw ``definition_yaml`` bakes the file's text byte-identically."""
    directory = tmp_path / "graphs" / "file-artifacts-graph"
    (directory / "prompts").mkdir(parents=True)
    (directory / "prompts" / "build.md").write_text("do the work")
    (directory / "reference-notes.md").write_text(_DOCKET_TEXT)
    graph_path = directory / "graph.yaml"
    graph_path.write_text(_FILE_ARTIFACTS_GRAPH)
    hub = build_hub(tmp_path)

    minted = hub.client.post("/api/graphs", json={"definition_yaml": GraphFile(graph_path).inlined_yaml})

    assert minted.status_code == 201, minted.text
    reloaded = hub.services.graphs.get(minted.json()["graph_id"])
    assert reloaded is not None
    assert reloaded.artifacts == [GraphArtifact(name="docket", content=_DOCKET_TEXT, ordinal=0)]


def test_reminting_the_same_definition_yaml_bakes_identical_artifact_content(tmp_path: Path) -> None:
    """Round-trip claim: posting the same already-inlined ``definition_yaml`` twice —
    the shape ``hub graph mint <path>`` sends on a re-mint — bakes byte-identical content
    into each mint, whatever else differs between the two graphs (their ids)."""
    hub = build_hub(tmp_path)
    first = hub.client.post("/api/graphs", json={"definition_yaml": _ARTIFACTS_GRAPH})
    second = hub.client.post("/api/graphs", json={"definition_yaml": _ARTIFACTS_GRAPH})
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    first_graph = hub.services.graphs.get(first.json()["graph_id"])
    second_graph = hub.services.graphs.get(second.json()["graph_id"])
    assert first_graph is not None
    assert second_graph is not None
    assert first_graph.artifacts == second_graph.artifacts


_ILLEGAL_ARTIFACT_NAME_GRAPH = """
name: illegal-artifact-name
entry: build
artifacts:
  ../escape: content
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


def test_mint_an_illegal_artifact_name_is_422_naming_the_entry(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _ILLEGAL_ARTIFACT_NAME_GRAPH})
    assert resp.status_code == 422
    report = resp.json()
    assert report["ok"] is False
    assert any("../escape" in e and "name must be" in e for e in report["errors"])


_COLLIDING_ARTIFACT_NAME_GRAPH = """
name: colliding-artifact-name
entry: review
artifacts:
  review-findings: content
nodes:
  review:
    executor: runner
    prompt: review with cold eyes
    produces:
      - review-findings
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""


def test_mint_an_artifact_name_colliding_with_a_produces_name_is_422_naming_the_entry(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _COLLIDING_ARTIFACT_NAME_GRAPH})
    assert resp.status_code == 422
    report = resp.json()
    assert report["ok"] is False
    assert any("review-findings" in e and "collides with a node's `produces:` name" in e for e in report["errors"])


# Non-alphabetical on purpose: an `order_by(name)` regression would still
# pass an alphabetically-sorted fixture, so this pins the authored ordinal, not the name.
_MULTI_ARTIFACTS_GRAPH = """
name: multi-artifacts-graph
entry: build
artifacts:
  zebra: z content
  apple: a content
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


def test_get_graph_lists_baked_artifact_names_in_authored_order_with_no_content(tmp_path: Path) -> None:
    """``GET /api/graphs/<id>`` serves the baked names in authored order and nothing else:
    the content is confirmable only through worker retrieval, never this surface."""
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _MULTI_ARTIFACTS_GRAPH})
    assert minted.status_code == 201, minted.text
    assert minted.json()["artifacts"] == ["zebra", "apple"]

    resp = hub.client.get(f"/api/graphs/{minted.json()['graph_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["artifacts"] == ["zebra", "apple"]
    # No content anywhere in the response — asserted, not merely absent from the shape.
    assert "z content" not in resp.text
    assert "a content" not in resp.text


def test_get_graph_with_no_artifacts_declared_lists_none(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _VALID_GRAPH})
    assert minted.status_code == 201, minted.text

    resp = hub.client.get(f"/api/graphs/{minted.json()['graph_id']}")
    assert resp.status_code == 200
    assert resp.json()["artifacts"] == []
