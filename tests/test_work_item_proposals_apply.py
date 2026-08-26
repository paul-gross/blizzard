"""Proposed work items through the apply path (component tier). A node-step's completion
may carry proposed work items alongside its artifacts, gated by the node's own
``proposes_work_items`` policy (D4, D6). Proposals ride exactly where artifacts are
written — the ordinary transition and the migration lane alike (D2) — and are inert at
landing time (no ``work_items`` row, no envelope/view surface) until the delivery-
materialization sweep reads one, once the chunk delivers (``tests/test_work_item_materialization.py``)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "9"}

_BUILD_ARTIFACT = {"name": "triage-notes", "kind": "asset", "content": "hand off"}

_POLICY_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    proposes_work_items: true
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

_NO_POLICY_YAML = _POLICY_YAML.replace("    proposes_work_items: true\n", "")

_MIGRATE_POLICY_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    proposes_work_items: true
    judgement:
      prompt: |
        Assess the build.
      choices:
        migrate:
          description: Hand off to triage.
          to: graph:triage
        fail:
          description: Retry.
          to: build
"""

_MIGRATE_NO_POLICY_YAML = _MIGRATE_POLICY_YAML.replace("    proposes_work_items: true\n", "")

_TRIAGE_YAML = """
name: triage
entry: build
nodes:
  build:
    executor: runner
    prompt: Triage.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: build
"""

_TARGET_WITH_DELIVER_YAML = """
name: triage
entry: build
nodes:
  build:
    executor: runner
    prompt: Triage.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: build
  deliver:
    executor: runner
    prompt: Triage deliver.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
        fail:
          description: Retry.
          to: deliver
"""

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
          description: Ready for signoff.
          to: approve-gate
        fail:
          description: Retry.
          to: build
  approve-gate:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Ship it.
          to: deliver
        reject:
          description: Send it back.
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


def _create_proposal(*, title: str = "New idea", body: str = "do it", stated_priority: str = "high") -> dict:
    return {"kind": "create", "title": title, "body": body, "stated_priority": stated_priority}


def _update_proposal(*, source: str = "default", ref: str = "42", evidence: str = "fixed") -> dict:
    return {"kind": "update", "source": source, "ref": ref, "evidence": evidence}


def _ingest(hub, yaml_body: str) -> tuple[str, dict]:  # type: ignore[no-untyped-def]
    """Mint the graph, ingest+promote+claim a chunk; return (chunk_id, name -> node_id)."""
    graph = hub.client.post("/api/graphs", json={"definition_yaml": yaml_body})
    assert graph.status_code == 201, graph.text
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, nodes


def _complete(
    hub,  # type: ignore[no-untyped-def]
    chunk_id: str,
    node_id: str,
    *,
    choice: str,
    epoch: int = 1,
    artifacts: list[dict] | None = None,
    proposals: list[dict] | None = None,
    decision_id: str | None = None,
) -> httpx.Response:
    body: dict = {
        "choice": choice,
        "epoch": epoch,
        "runner_id": "r1",
        "from_node_id": node_id,
        "artifacts": artifacts if artifacts is not None else [],
        "proposals": proposals if proposals is not None else [],
    }
    if decision_id is not None:
        body["decision_id"] = decision_id
    return hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=body)


def _submit_decision(
    hub,  # type: ignore[no-untyped-def]
    chunk_id: str,
    node_id: str,
    *,
    epoch: int = 1,
    artifacts: list[dict] | None = None,
    proposals: list[dict] | None = None,
) -> httpx.Response:
    """A runner-config gate: park ``node_id`` on a decision instead of transitioning."""
    return hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/decisions",
        json={
            "from_node_id": node_id,
            "epoch": epoch,
            "runner_id": "r1",
            "artifacts": artifacts if artifacts is not None else [],
            "proposals": proposals if proposals is not None else [],
        },
    )


def _stored_proposals(hub, chunk_id: str) -> list:  # type: ignore[no-untyped-def]
    from blizzard.hub.store import schema as s

    with hub.engine.connect() as conn:
        rows = conn.execute(
            select(s.work_item_proposals)
            .where(s.work_item_proposals.c.chunk_id == chunk_id)
            .order_by(s.work_item_proposals.c.ordinal)
        ).all()
    return list(rows)


def test_proposals_land_with_the_transition_in_authored_order(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _POLICY_YAML)

    proposals = [_create_proposal(title="first"), _update_proposal(ref="1"), _create_proposal(title="third")]
    resp = _complete(hub, chunk_id, nodes["build"], choice="pass", artifacts=[_BUILD_ARTIFACT], proposals=proposals)

    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] != "failure"
    rows = _stored_proposals(hub, chunk_id)
    assert [r.ordinal for r in rows] == [0, 1, 2]
    assert [r.kind for r in rows] == ["create", "update", "create"]
    assert rows[0].node_name == "build" and rows[0].epoch == 1
    assert "first" in rows[0].data
    assert "third" in rows[2].data


def test_a_replayed_completion_writes_no_second_proposal_set(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _POLICY_YAML)
    proposals = [_create_proposal()]

    first = _complete(hub, chunk_id, nodes["build"], choice="pass", proposals=proposals)
    assert first.status_code == 200 and first.json()["outcome"] != "failure"
    replay = _complete(hub, chunk_id, nodes["build"], choice="pass", proposals=proposals)
    assert replay.status_code == 200

    assert len(_stored_proposals(hub, chunk_id)) == 1


def test_a_stale_epoch_completion_writes_no_proposals_and_no_transition(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _POLICY_YAML)
    # Advance the fence to epoch 2 with a fresh lease, then submit at the stale epoch 1.
    report_lease(hub, chunk_id, epoch=2, seq=2)

    stale = _complete(hub, chunk_id, nodes["build"], choice="pass", epoch=1, proposals=[_create_proposal()])
    assert stale.json()["outcome"] == "failure"
    assert "stale epoch" in stale.json()["detail"]
    assert _stored_proposals(hub, chunk_id) == []


def test_a_migration_carrying_proposals_stores_them_with_the_migration_fact(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _TRIAGE_YAML}).status_code == 201
    chunk_id, nodes = _ingest(hub, _MIGRATE_POLICY_YAML)

    resp = _complete(
        hub,
        chunk_id,
        nodes["build"],
        choice="migrate",
        artifacts=[_BUILD_ARTIFACT],
        proposals=[_create_proposal()],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "migrated", resp.text
    rows = _stored_proposals(hub, chunk_id)
    assert len(rows) == 1
    assert rows[0].kind == "create"


def test_proposals_from_a_node_with_no_policy_are_rejected_leaving_everything_untouched(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _NO_POLICY_YAML)

    resp = _complete(
        hub, chunk_id, nodes["build"], choice="pass", artifacts=[_BUILD_ARTIFACT], proposals=[_create_proposal()]
    )

    assert resp.status_code == 200, resp.text  # ApplyResponse — a semantic failure
    assert resp.json()["outcome"] == "failure"
    assert "`build`" in resp.json()["detail"]
    assert "proposes_work_items" in resp.json()["detail"]
    # The fence never advanced, no transition, no artifact, no proposal.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["latest_epoch"] == 1
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["artifacts"] == []
    assert _stored_proposals(hub, chunk_id) == []

    # A follow-up completion at the same epoch, with no proposals, still succeeds —
    # the rejection left the fence untouched.
    retried = _complete(hub, chunk_id, nodes["build"], choice="pass", artifacts=[_BUILD_ARTIFACT])
    assert retried.status_code == 200, retried.text
    assert retried.json()["outcome"] != "failure"


def test_the_refusal_holds_on_an_authored_cross_graph_edge(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _TRIAGE_YAML}).status_code == 201
    chunk_id, nodes = _ingest(hub, _MIGRATE_NO_POLICY_YAML)

    resp = _complete(hub, chunk_id, nodes["build"], choice="migrate", proposals=[_create_proposal()])

    assert resp.json()["outcome"] == "failure"
    assert "`build`" in resp.json()["detail"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["latest_epoch"] == 1
    assert _stored_proposals(hub, chunk_id) == []


def test_the_refusal_holds_on_a_runner_config_gate_submission(tmp_path: Path) -> None:
    """The fourth dispatch fork: a runner-config gate's own wire body never reaches
    ``ApplyService.apply`` at all, so ``DecisionService.submit`` must run the same check."""
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _NO_POLICY_YAML)

    resp = _submit_decision(hub, chunk_id, nodes["build"], artifacts=[_BUILD_ARTIFACT], proposals=[_create_proposal()])

    assert resp.status_code == 200, resp.text  # ApplyResponse — a semantic failure
    assert resp.json()["outcome"] == "failure"
    assert "`build`" in resp.json()["detail"]
    assert "proposes_work_items" in resp.json()["detail"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"] is None
    assert _stored_proposals(hub, chunk_id) == []

    # A follow-up decision submission at the same epoch, with no proposals, still parks —
    # the rejection left the fence and any open decision untouched.
    retried = _submit_decision(hub, chunk_id, nodes["build"], artifacts=[_BUILD_ARTIFACT])
    assert retried.json()["outcome"] == "parked_at_gate", retried.text


def test_the_refusal_holds_on_a_decision_id_resolving_completion(tmp_path: Path) -> None:
    """A gate node can never declare ``proposes_work_items`` (D4) — so a resolving
    transition out of it carrying proposals is refused before the gate-resolution
    dispatch, which itself would silently ignore them (D2)."""
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _GATE_YAML)
    build_resp = _complete(hub, chunk_id, nodes["build"], choice="pass", artifacts=[_BUILD_ARTIFACT])
    assert build_resp.json()["outcome"] == "parked_at_gate", build_resp.text
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]
    resolved = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"})
    assert resolved.status_code == 200, resolved.text

    resp = _complete(
        hub,
        chunk_id,
        nodes["approve-gate"],
        choice="approve",
        decision_id=decision_id,
        proposals=[_create_proposal()],
    )

    assert resp.json()["outcome"] == "failure"
    assert "`approve-gate`" in resp.json()["detail"]
    assert _stored_proposals(hub, chunk_id) == []
    # The decision itself is unaffected — a follow-up resolving completion without
    # proposals still closes it.
    retried = _complete(hub, chunk_id, nodes["approve-gate"], choice="approve", decision_id=decision_id)
    assert retried.json()["outcome"] == "hub_node_taken", retried.text


def test_a_runner_config_gates_proposals_land_with_the_decision_not_its_resolution(tmp_path: Path) -> None:
    """Unlike the graph gate above, this parks a *worker-judged* node the policy is legal
    on (D4), so the resolving completion isn't refused — but its proposals still don't
    land: the decision's own submission is where they belong, same as its artifacts (D2)."""
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _POLICY_YAML)

    parked = _submit_decision(hub, chunk_id, nodes["build"], proposals=[_create_proposal(title="landed-with-decision")])
    assert parked.json()["outcome"] == "parked_at_gate", parked.text
    assert [r.data for r in _stored_proposals(hub, chunk_id) if "landed-with-decision" in r.data]
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]
    assert hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "pass"}).status_code == 200

    resolving = _complete(
        hub,
        chunk_id,
        nodes["build"],
        choice="pass",
        decision_id=decision_id,
        proposals=[_create_proposal(title="must-not-land")],
    )

    assert resolving.json()["outcome"] != "failure", resolving.text
    rows = _stored_proposals(hub, chunk_id)
    assert len(rows) == 1
    assert "landed-with-decision" in rows[0].data


def test_the_gate_resolutions_migration_consult_also_drops_the_resolving_completions_proposals(
    tmp_path: Path,
) -> None:
    """The migration-time consult fires from inside gate resolution too (issue #124) — its
    own dispatch fork must drop the resolving completion's proposals exactly like the
    plain transition beside it, not just persist whatever it was handed (D2)."""
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _POLICY_YAML)
    triage = hub.client.post("/api/graphs", json={"definition_yaml": _TARGET_WITH_DELIVER_YAML})
    assert triage.status_code == 201, triage.text
    triage_id = triage.json()["graph_id"]
    intent = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": triage_id}})
    assert intent.status_code == 202, intent.text

    parked = _submit_decision(hub, chunk_id, nodes["build"], proposals=[_create_proposal(title="landed-with-decision")])
    assert parked.json()["outcome"] == "parked_at_gate", parked.text
    decision_id = hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"]
    assert hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "pass"}).status_code == 200

    resolving = _complete(
        hub,
        chunk_id,
        nodes["build"],
        choice="pass",
        decision_id=decision_id,
        proposals=[_create_proposal(title="must-not-land")],
    )

    assert resolving.json()["outcome"] == "migrated", resolving.text
    rows = _stored_proposals(hub, chunk_id)
    assert len(rows) == 1
    assert "landed-with-decision" in rows[0].data


def test_a_malformed_proposal_payload_is_refused_at_the_wired_hubs_edge(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _POLICY_YAML)

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": nodes["build"],
            "artifacts": [],
            "proposals": [{"kind": "create", "body": "no title"}],
        },
    )

    assert resp.status_code == 422, resp.text


def test_proposals_ride_the_completion_inertly_then_materialize_once_swept(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _ingest(hub, _POLICY_YAML)

    resp = _complete(
        hub, chunk_id, nodes["build"], choice="pass", artifacts=[_BUILD_ARTIFACT], proposals=[_create_proposal()]
    )
    assert resp.status_code == 200, resp.text
    assert "proposals" not in resp.json()
    assert "proposal" not in str(resp.json().get("next_envelope", {})).lower()

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert all(a["name"] != "New idea" for a in detail["artifacts"])
    assert "proposal" not in str(detail).lower()

    # The chunk's own `deliver` hub node already ran synchronously above, so it has
    # delivered — but nothing materializes until the sweep itself runs.
    assert hub.client.get("/api/work-sources/hub/items").json()["items"] == []

    hub.services.work_item_materialization.sweep()

    items = hub.client.get("/api/work-sources/hub/items").json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "New idea"
