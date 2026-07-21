"""``POST /api/decisions/{id}/resolutions`` and the deprecated singular
``.../resolution`` alias (issue #104), component tier.

``tests/test_gates.py`` covers gate mechanics end to end (graph gate, runner-config
gate, first-write-wins) against the pre-#104 singular path. This file pins the pluralized
successor's identical CAS behavior (200 winner / 409 loser) and proves the singular
alias still resolves byte-identically while carrying the ``Deprecation``/``Link``
headers and ``deprecated: true`` in the OpenAPI operation; a runner bearer token is
still rejected on both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "104"}

_GATE_YAML = """
name: default-delivery
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
          to: approve-gate
        fail:
          description: Incomplete.
          to: build
  approve-gate:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Ship it — proceed to delivery.
          to: deliver
        reject:
          description: Send it back to build.
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

_BUILD_ARTIFACT = {
    "name": "acme/widget",
    "kind": "git_commit",
    "repo": "acme/widget",
    "branch_name": "b",
    "commit_hash": "c",
}


def _open_decision(hub) -> str:  # type: ignore[no-untyped-def]
    """Mint the gated graph, ingest+promote+claim+lease a chunk, and drive it to an
    open decision; return the decision id."""
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    assert graph.status_code == 201, graph.text
    build_node_id = next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "artifacts": [_BUILD_ARTIFACT],
        },
    )
    assert resp.json()["outcome"] == "parked_at_gate", resp.text
    decision = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]
    assert decision is not None
    return str(decision["decision_id"])


# --- POST /api/decisions/{id}/resolutions — primary -------------------------


def test_resolutions_resolves_first_write_wins(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    decision_id = _open_decision(hub)

    first = hub.client.post(
        f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve", "resolved_by": "ada"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["choice"] == "approve"
    # `resolved_by` in the body is a spoof attempt — issue #91 overwrites it with the
    # resolved session identity, `"operator"` under the default `auth.mode = "none"`.
    assert first.json()["resolved_by"] == "operator"

    second = hub.client.post(
        f"/api/decisions/{decision_id}/resolutions", json={"choice": "reject", "resolved_by": "bob"}
    )
    assert second.status_code == 409, second.text
    assert second.json()["already_resolved_by"] == "operator"


def test_resolutions_unknown_decision_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/decisions/dc_missing/resolutions", json={"choice": "approve"})
    assert resp.status_code == 404


def test_resolutions_unknown_choice_is_400(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    decision_id = _open_decision(hub)
    resp = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "maybe"})
    assert resp.status_code == 400


# --- Runner principal is still rejected on the resolution route -------------


def test_runner_bearer_token_is_rejected_on_resolutions(tmp_path: Path) -> None:
    from blizzard.hub.config import RUNNER_AUTH_ENFORCE
    from tests.test_fleet_auth import _bearer, _seed_enrolled

    token = _seed_enrolled(tmp_path)
    warn_hub = build_hub(tmp_path)
    decision_id = _open_decision(warn_hub)

    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)
    assert (
        hub.client.post(
            f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"}, headers=_bearer(token)
        ).status_code
        == 403
    )
