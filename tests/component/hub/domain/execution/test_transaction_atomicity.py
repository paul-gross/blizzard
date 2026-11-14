"""Execution writes and their subsequent reads share a rollback boundary."""

from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.execution import apply as apply_module
from tests.support.gate_decisions import (
    _open_decision,  # pyright: ignore[reportPrivateUsage]  # shared test helper
)
from tests.support.scaffolding import build_hub, ingest, report_escalation, report_lease


def test_transition_is_visible_to_gate_arrival_and_rolled_back_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    seen: list[tuple[str, str]] = []

    def fail(decisions: Any, facts: Any, transaction: Any, clock: Any, **kwargs: Any) -> tuple[int, ...]:
        current = facts.load_facts(kwargs["chunk_id"], transaction=transaction)
        assert current is not None
        assert current.current_node_id() == kwargs["node_id"]
        seen.append((kwargs["chunk_id"], kwargs["node_id"]))
        raise RuntimeError("gate failure")

    monkeypatch.setattr(apply_module, "open_graph_gate_on_arrival", fail)
    with pytest.raises(RuntimeError, match="gate failure"):
        _open_decision(hub)
    [(chunk_id, gate_node)] = seen
    after = hub.services.chunks.facts.load_facts(chunk_id)
    assert after is not None
    assert after.current_node_id() != gate_node
    assert not after.transitions


def test_requeue_rolls_back_when_route_release_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    route = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert route.status_code == 201
    report_lease(hub, chunk_id, epoch=1, seq=1)
    assert report_escalation(hub, chunk_id, epoch=1, seq=2)["applied"] == [2]
    before = hub.services.chunks.facts.load_facts(chunk_id)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None

    def fail(*args: Any, **kwargs: Any) -> None:
        facts = hub.services.chunks.facts.load_facts(chunk_id, transaction=kwargs["transaction"])
        assert facts is not None and len(facts.requeues) == 1
        raise RuntimeError("release failure")

    monkeypatch.setattr(hub.services.chunks.route, "record_route_released", fail)
    with pytest.raises(RuntimeError, match="release failure"):
        hub.services.requeue.requeue(chunk, by="operator")
    assert hub.services.chunks.facts.load_facts(chunk_id) == before
    assert hub.services.chunks.route.route_of(chunk_id) is not None


@pytest.mark.parametrize("abort", [False, True], ids=["commit", "rollback"])
def test_migration_landing_and_gate_arrival_share_one_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, abort: bool
) -> None:
    hub = build_hub(tmp_path)
    source = hub.client.post(
        "/api/graphs",
        json={
            "definition_yaml": """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    judgement:
      prompt: Assess.
      choices:
        hand-off:
          description: Hand off.
          to: graph:other
"""
        },
    )
    assert source.status_code == 201, source.text
    target = hub.client.post(
        "/api/graphs",
        json={
            "definition_yaml": """
name: other
entry: signoff
nodes:
  signoff:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Approved.
          to: done
        reject:
          description: Rejected.
          to: signoff
"""
        },
    )
    assert target.status_code == 201, target.text
    source_node = source.json()["nodes"][0]["node_id"]
    target_node = target.json()["nodes"][0]["node_id"]
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    route = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert route.status_code == 201, route.text
    report_lease(hub, chunk_id, epoch=1, seq=1)
    before = hub.services.chunks.facts.load_facts(chunk_id)
    before_record = hub.services.chunks.record.get(chunk_id)
    assert before is not None and before_record is not None
    original = apply_module.open_graph_gate_on_arrival
    observed: list[bool] = []

    def arrival(decisions: Any, facts: Any, transaction: Any, clock: Any, **kwargs: Any) -> tuple[int, ...]:
        landed = facts.load_facts(chunk_id, transaction=transaction)
        record = hub.services.chunks.record.get(chunk_id, transaction=transaction)
        assert landed is not None and len(landed.migrations) == 1
        assert landed.current_node_id() == target_node
        assert record is not None and record.graph_id == target.json()["graph_id"]
        result = original(decisions, facts, transaction, clock, **kwargs)
        with_decision = facts.load_facts(chunk_id, transaction=transaction)
        assert with_decision is not None and len(with_decision.decisions) == 1
        observed.append(True)
        if abort:
            raise RuntimeError("abort migration")
        return result

    monkeypatch.setattr(apply_module, "open_graph_gate_on_arrival", arrival)

    def complete() -> Any:
        return hub.client.post(
            f"/api/fleet/chunks/{chunk_id}/completions",
            json={"choice": "hand-off", "epoch": 1, "runner_id": "r1", "from_node_id": source_node, "artifacts": []},
        )

    if abort:
        with pytest.raises(RuntimeError, match="abort migration"):
            complete()
        assert hub.services.chunks.facts.load_facts(chunk_id) == before
        assert hub.services.chunks.record.get(chunk_id) == before_record
        assert hub.services.chunks.route.route_of(chunk_id) is not None
    else:
        response = complete()
        assert response.status_code == 200, response.text
        committed = hub.services.chunks.facts.load_facts(chunk_id)
        assert committed is not None and len(committed.migrations) == 1 and len(committed.decisions) == 1
        assert committed.current_node_id() == target_node
    assert observed == [True]
