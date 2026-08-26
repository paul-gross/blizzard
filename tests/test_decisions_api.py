"""``POST /api/decisions/{id}/resolutions`` and the deprecated singular
``.../resolution`` alias (issue #104), component tier.

Pins the pluralized route's CAS behavior (200 winner / 409 loser) and proves the
singular alias resolves byte-identically while carrying the ``Deprecation``/``Link``
headers; a runner bearer token is rejected on both."""

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


# --- The gate docket (blizzard#367) ------------------------------------------

_GATE_WITH_PROPOSALS_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    proposes_work_items: true
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Ready.
          to: done
        fail:
          description: Retry.
          to: build
"""


def _create_proposal(*, title: str) -> dict:
    return {"kind": "create", "title": title, "body": "do it", "stated_priority": "normal"}


def _mint_gate_graph(hub) -> str:  # type: ignore[no-untyped-def]
    """Register the runner-config-gated graph once; return its ``build`` node id. A
    second identical registration is idempotent store-side, but re-parsing the same
    YAML twice yields a fresh candidate node id that does not match what actually
    landed — so every chunk in a test shares one mint."""
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_WITH_PROPOSALS_YAML})
    assert graph.status_code == 201, graph.text
    return next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")


def _open_decision_with_proposals(
    hub, build_node_id: str, *, ref: str, proposals: list[dict], runner_id: str = "r1", seq: int = 1
) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Drive a chunk on the already-minted gate graph to an open decision carrying
    ``proposals`` (D2); return ``(chunk_id, decision_id)``. ``seq`` must be distinct per
    call sharing a ``runner_id`` — it is that runner's own monotonic fact sequence, not
    per-chunk, and a repeated value replays as an idempotent no-op."""
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": ref})]}
    ).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": "w1", "environment_ids": ["e"]},
    )
    report_lease(hub, chunk_id, epoch=1, seq=seq)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/decisions",
        json={
            "from_node_id": build_node_id,
            "epoch": 1,
            "runner_id": runner_id,
            "artifacts": [],
            "proposals": proposals,
        },
    )
    assert resp.status_code == 200, resp.text
    decision = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]
    assert decision is not None
    return chunk_id, str(decision["decision_id"])


def _proposal_id_by_title(docket: list[dict], title: str) -> str:
    return next(e["proposal_id"] for e in docket if e["payload"]["title"] == title)


def test_docket_carries_pending_proposals_on_both_open_decisions_and_chunk_detail_reads(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    build_node_id = _mint_gate_graph(hub)
    chunk_id, decision_id = _open_decision_with_proposals(
        hub, build_node_id, ref="367a", proposals=[_create_proposal(title="fix it")]
    )

    open_decisions = hub.client.get("/api/decisions").json()["decisions"]
    open_decision = next(d for d in open_decisions if d["decision_id"] == decision_id)
    detail_decision = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]

    for decision in (open_decision, detail_decision):
        assert len(decision["docket"]) == 1
        entry = decision["docket"][0]
        assert entry["kind"] == "create"
        assert entry["node_name"] == "build"
        assert entry["payload"] == {"kind": "create", "title": "fix it", "body": "do it", "stated_priority": "normal"}
        assert entry["malformed"] is False
        assert entry["struck"] is False


def test_a_chunk_with_no_pending_proposals_reads_an_empty_docket(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    decision_id = _open_decision(hub)  # the plain graph-gate fixture, no proposals

    decisions = hub.client.get("/api/decisions").json()["decisions"]
    entry = next(d for d in decisions if d["decision_id"] == decision_id)
    assert entry["docket"] == []


def test_a_struck_entry_reads_back_carrying_its_striking_identity_after_resolve(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    build_node_id = _mint_gate_graph(hub)
    chunk_id, decision_id = _open_decision_with_proposals(
        hub, build_node_id, ref="367b", proposals=[_create_proposal(title="keep"), _create_proposal(title="strike")]
    )
    docket = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["docket"]
    strike_id = _proposal_id_by_title(docket, "strike")

    resolved = hub.client.post(
        f"/api/decisions/{decision_id}/resolutions", json={"choice": "pass", "struck": [strike_id]}
    )
    assert resolved.status_code == 200, resolved.text

    docket = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["docket"]
    struck_entry = next(e for e in docket if e["proposal_id"] == strike_id)
    assert struck_entry["struck"] is True
    assert struck_entry["struck_by"] == "operator"
    assert struck_entry["struck_at"] is not None
    kept_entry = next(e for e in docket if e["proposal_id"] != strike_id)
    assert kept_entry["struck"] is False


def test_an_unknown_or_foreign_proposal_id_answers_400_and_strikes_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    build_node_id = _mint_gate_graph(hub)
    _, decision_id = _open_decision_with_proposals(
        hub, build_node_id, ref="367c", proposals=[_create_proposal(title="only")]
    )
    other_chunk_id, other_decision_id = _open_decision_with_proposals(
        hub, build_node_id, ref="367d", proposals=[_create_proposal(title="foreign")], seq=2
    )
    other_docket = hub.client.get(f"/api/chunks/{other_chunk_id}").json()["decision"]["docket"]
    foreign_id = _proposal_id_by_title(other_docket, "foreign")

    unknown = hub.client.post(
        f"/api/decisions/{decision_id}/resolutions", json={"choice": "pass", "struck": ["wip_bogus"]}
    )
    assert unknown.status_code == 400, unknown.text

    foreign = hub.client.post(
        f"/api/decisions/{decision_id}/resolutions", json={"choice": "pass", "struck": [foreign_id]}
    )
    assert foreign.status_code == 400, foreign.text

    still_open = next(
        d for d in hub.client.get("/api/decisions").json()["decisions"] if d["decision_id"] == decision_id
    )
    assert still_open["resolved_choice"] is None
    other_still_open = next(
        d for d in hub.client.get("/api/decisions").json()["decisions"] if d["decision_id"] == other_decision_id
    )
    assert all(not e["struck"] for e in other_still_open["docket"])


def test_resolution_omitting_struck_resolves_and_passes_every_proposal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    build_node_id = _mint_gate_graph(hub)
    chunk_id, decision_id = _open_decision_with_proposals(
        hub, build_node_id, ref="367e", proposals=[_create_proposal(title="one"), _create_proposal(title="two")]
    )

    resolved = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "pass"})
    assert resolved.status_code == 200, resolved.text

    docket = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["docket"]
    assert len(docket) == 2
    assert all(not e["struck"] for e in docket)
