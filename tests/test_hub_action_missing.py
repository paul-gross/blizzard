"""A hub node naming no action, and an answer no edge routes, each escalate their chunk to a person once, at the
next epoch, rather than stalling it; the boot pass reaches a chunk already standing at such a node; the invariant
checker names the node; and a graph minted now stores no ``run`` (component tier)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
import yaml
from sqlalchemy import select, update

from blizzard.hub.domain.chunk.model import WorkRef, decide_close_enqueue
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.graph.model import GraphDoc
from blizzard.hub.domain.graph.validation import Validator
from blizzard.hub.store import schema as s
from blizzard.tools.invariants import hub_invariants
from tests.support import TEST_CATALOGUE, HubHarness, build_hub, hub_scoped, pointer_token, report_lease

pytestmark = pytest.mark.component

_LEGACY_RUN = json.dumps([{"command": "./ship.sh --now", "name": "ship", "produces": None}])

_GRAPH_YAML = """
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
          to: merge
  merge:
    executor: hub
    action: test.answer
    with: {choice: [success]}
    judgement:
      choices:
        success:
          description: Landed.
          to: done
        failure:
          description: Failed to land.
          to: build
"""


def _claimed(hub: HubHarness, graph_yaml: str = _GRAPH_YAML) -> tuple[str, str, str]:
    """Mint ``graph_yaml`` and claim a chunk on it at ``build``: ``(chunk_id, build_node_id, graph_id)``."""
    minted = hub.client.post("/api/graphs", json={"definition_yaml": graph_yaml})
    assert minted.status_code == 201, minted.text
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "7"})]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert claim.status_code == 201, claim.text
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, claim.json()["envelope"]["node"]["node_id"], minted.json()["graph_id"]


def _strip_action(hub: HubHarness, graph_id: str, legacy_run: str | None) -> None:
    """Leave ``merge`` as the carry-over leaves a node it could not translate: no action, its ``run`` kept."""
    with hub.engine.begin() as conn:
        conn.execute(
            hub_scoped(
                update(s.graph_nodes)
                .where(s.graph_nodes.c.graph_id == graph_id, s.graph_nodes.c.name == "merge")
                .values(action=None, action_args=None, legacy_run=legacy_run)
            )
        )


def _pass_build(hub: HubHarness, chunk_id: str, build_node_id: str) -> str:
    applied = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "check_results": [],
            "artifacts": [],
        },
    )
    assert applied.status_code == 200, applied.text
    return applied.json()["outcome"]


def _park_at_merge(hub: HubHarness, chunk_id: str, build_node_id: str, graph_id: str) -> None:
    """Move the chunk onto ``merge`` without driving it — as a chunk stood when the hub was stopped."""
    graph = hub.services.graphs.get(graph_id)
    assert graph is not None
    merge = graph.node_by_name("merge")
    assert merge is not None
    cast(IWriteChunkMovementRepository, hub.services.chunks.movement).record_transition(
        transition_id="tr_parked",
        close_decision=decide_close_enqueue([WorkRef("default", "7")], lambda _: True),
        chunk_id=chunk_id,
        from_node_id=build_node_id,
        to_node_id=merge.node_id,
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=hub.clock.now(),
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
    )


def _escalations(hub: HubHarness, chunk_id: str) -> list[tuple[int, str, str]]:
    with hub.engine.begin() as conn:
        rows = conn.execute(
            hub_scoped(
                select(s.escalations).where(s.escalations.c.chunk_id == chunk_id).order_by(s.escalations.c.epoch)
            )
        ).all()
    return [(row.epoch, row.cause, row.detail) for row in rows]


def _events(hub: HubHarness, chunk_id: str, kind: str) -> list[object]:
    return [e for e in hub.services.chunks.events.list_events(chunk_id=chunk_id) if e.kind == kind]


def _advance(hub: HubHarness, chunk_id: str) -> dict[str, object]:
    advanced = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert advanced.status_code == 200, advanced.text
    return advanced.json()


def _assert_escalated_once(hub: HubHarness, chunk_id: str, *, naming: str) -> None:
    ((epoch, cause, detail),) = _escalations(hub, chunk_id)
    assert (epoch, cause) == (2, "hub-action-missing")
    assert naming in detail
    (event,) = _events(hub, chunk_id, "hub-action-missing")
    assert event.severity == "critical"  # type: ignore[attr-defined]
    detail_json = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail_json["status"] == "needs_human"
    assert detail_json["escalation"]["cause"] == "hub-action-missing"


# --- A node naming no action ---------------------------------------------------


def test_a_chunk_reaching_a_node_with_no_action_escalates_once_naming_its_stored_run(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, graph_id = _claimed(hub)
    _strip_action(hub, graph_id, _LEGACY_RUN)

    assert _pass_build(hub, chunk_id, build_node_id) == "hub_node_taken"
    hub.settle_turns()

    _assert_escalated_once(hub, chunk_id, naming="./ship.sh --now")
    # Driven again, nothing new is written.
    assert _advance(hub, chunk_id)["ran"] is False
    hub.settle_turns()
    _assert_escalated_once(hub, chunk_id, naming="./ship.sh --now")


def test_the_reconcile_escalates_a_parked_node_with_no_action_once(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, graph_id = _claimed(hub)
    _strip_action(hub, graph_id, None)
    _park_at_merge(hub, chunk_id, build_node_id, graph_id)

    hub.settle_turns()
    hub.flow_driver.wake()
    hub.settle_turns()

    assert _advance(hub, chunk_id)["detail"] == "escalated — awaiting a person"
    _assert_escalated_once(hub, chunk_id, naming="no `run:`")


def test_the_boot_pass_escalates_a_chunk_standing_at_a_node_with_no_action(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, graph_id = _claimed(hub)
    _strip_action(hub, graph_id, _LEGACY_RUN)
    _park_at_merge(hub, chunk_id, build_node_id, graph_id)

    assert hub.services.flow_lines.escalate_actionless() == 1

    _assert_escalated_once(hub, chunk_id, naming="./ship.sh --now")
    # A second boot, and a tick after it, write nothing.
    assert hub.services.flow_lines.escalate_actionless() == 0
    hub.settle_turns()
    _assert_escalated_once(hub, chunk_id, naming="./ship.sh --now")


def test_the_boot_pass_leaves_a_chunk_pinned_to_the_graph_but_standing_elsewhere(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _build_node_id, graph_id = _claimed(hub)
    _strip_action(hub, graph_id, _LEGACY_RUN)

    assert hub.services.flow_lines.escalate_actionless() == 0

    assert _escalations(hub, chunk_id) == []


def test_the_invariant_names_a_hub_node_with_no_action_pinning_a_live_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _chunk_id, _build_node_id, graph_id = _claimed(hub)
    assert [v for v in hub_invariants(hub.engine).run() if v.invariant == "hub:hub-node-has-action"] == []

    _strip_action(hub, graph_id, _LEGACY_RUN)

    (violation,) = [v for v in hub_invariants(hub.engine).run() if v.invariant == "hub:hub-node-has-action"]
    assert graph_id in violation.detail
    assert "'merge'" in violation.detail


def test_a_graph_minted_now_stores_no_run(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _chunk_id, _build_node_id, graph_id = _claimed(hub)

    with hub.engine.begin() as conn:
        rows = conn.execute(
            hub_scoped(
                select(s.graph_nodes.c.name, s.graph_nodes.c.run, s.graph_nodes.c.legacy_run).where(
                    s.graph_nodes.c.graph_id == graph_id
                )
            )
        ).all()

    assert sorted(tuple(row) for row in rows) == [("build", None, None), ("merge", None, None)]


# --- An answer no edge routes --------------------------------------------------

_UNROUTED_YAML = _GRAPH_YAML.replace(
    "    action: test.answer\n    with: {choice: [success]}",
    "    action: test.sequence\n    with: {choices: [landed, success]}",
)


def test_an_unrouted_answer_escalates_at_once_naming_the_choice_and_takes_no_second_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A carried-over version may never route an answer its action gives; mint refuses one now, so the stored
    version is minted past validation."""
    assert Validator.of(GraphDoc.of(yaml.safe_load(_UNROUTED_YAML)), TEST_CATALOGUE).result.errors
    monkeypatch.setattr(Validator, "require_valid", lambda self: self.result)
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph_id = _claimed(hub, _UNROUTED_YAML)

    assert _pass_build(hub, chunk_id, build_node_id) == "hub_node_taken"
    hub.settle_turns()

    ((epoch, cause, detail),) = _escalations(hub, chunk_id)
    assert (epoch, cause) == (2, "unroutable-outcome")
    assert "`landed`" in detail
    assert len(_events(hub, chunk_id, "hub-node-unroutable-outcome")) == 1
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "needs_human"

    assert _advance(hub, chunk_id)["ran"] is False
    hub.settle_turns()

    names = [a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)]
    assert sorted(n for n in names if n.startswith("test-sequence/")) == ["test-sequence/0"]
    assert len(_escalations(hub, chunk_id)) == 1
    assert len(_events(hub, chunk_id, "hub-node-unroutable-outcome")) == 1
    assert hub.services.flows.open_turns() == []
