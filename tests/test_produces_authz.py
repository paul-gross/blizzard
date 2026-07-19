"""Produces-artifact authorization at the wired hub (component tier, issue #113 phase 5).

``tests/test_produces_auth.py`` covers ``check_produces`` itself (unit tier). This file
proves the **apply-path backstop**: a completion whose ``build`` node declares
``produces: [notes]`` is accepted under ``produces_mode=warn`` regardless of whether
``notes`` was explicitly attached (the assessment fallback still lands), and rejected
under ``produces_mode=enforce`` unless the submission carries an explicit
(``attached=True``) artifact for every declared name — leaving the fence and the
transition untouched on rejection, mirroring ``test_route_token_authz.py``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from blizzard.hub.config import PRODUCES_ENFORCE
from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "113"}

_GRAPH_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    produces:
      - notes
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
"""


def _ingest(hub) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Mint the graph, ingest+promote+claim a chunk; return (chunk_id, build node_id)."""
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML})
    assert graph.status_code == 201, graph.text
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    node_id = claim.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, node_id


def _submit(
    hub,  # type: ignore[no-untyped-def]
    chunk_id: str,
    node_id: str,
    *,
    epoch: int = 1,
    runner_id: str = "r1",
    artifacts: list[dict] | None = None,
) -> httpx.Response:
    body = {
        "choice": "pass",
        "epoch": epoch,
        "runner_id": runner_id,
        "from_node_id": node_id,
        "artifacts": artifacts if artifacts is not None else [],
    }
    return hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=body)


def test_a_fallback_only_completion_is_accepted_under_warn(tmp_path: Path) -> None:
    """``warn`` is the default: a completion missing an explicit `notes` attachment
    still applies (the assessment fallback still lands as content elsewhere)."""
    hub = build_hub(tmp_path)
    chunk_id, node_id = _ingest(hub)

    resp = _submit(hub, chunk_id, node_id, artifacts=[])

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] != "failure"


def test_a_fallback_only_completion_is_rejected_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, produces_mode=PRODUCES_ENFORCE)
    chunk_id, node_id = _ingest(hub)

    resp = _submit(hub, chunk_id, node_id, artifacts=[{"name": "notes", "kind": "asset", "content": "meh"}])

    assert resp.status_code == 200, resp.text  # ApplyResponse — a semantic failure, not an HTTP error
    assert resp.json()["outcome"] == "failure"
    assert "notes" in resp.json()["detail"]


def test_a_missing_produces_name_is_rejected_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, produces_mode=PRODUCES_ENFORCE)
    chunk_id, node_id = _ingest(hub)

    resp = _submit(hub, chunk_id, node_id, artifacts=[])

    assert resp.json()["outcome"] == "failure"


def test_an_explicitly_attached_artifact_is_accepted_under_enforce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, produces_mode=PRODUCES_ENFORCE)
    chunk_id, node_id = _ingest(hub)

    resp = _submit(
        hub,
        chunk_id,
        node_id,
        artifacts=[{"name": "notes", "kind": "asset", "content": "the real thing", "attached": True}],
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] != "failure"


def test_the_rejection_under_enforce_leaves_the_fence_and_transition_untouched(tmp_path: Path) -> None:
    """A rejected completion under ``enforce`` records no transition — a follow-up
    completion at the same epoch, this time with the explicit attachment, still
    succeeds; a partial apply would have already advanced the fence and made the
    retry stale."""
    hub = build_hub(tmp_path, produces_mode=PRODUCES_ENFORCE)
    chunk_id, node_id = _ingest(hub)

    rejected = _submit(hub, chunk_id, node_id, artifacts=[])
    assert rejected.json()["outcome"] == "failure"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["latest_epoch"] == 1

    retried = _submit(
        hub,
        chunk_id,
        node_id,
        artifacts=[{"name": "notes", "kind": "asset", "content": "the real thing", "attached": True}],
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["outcome"] != "failure"
