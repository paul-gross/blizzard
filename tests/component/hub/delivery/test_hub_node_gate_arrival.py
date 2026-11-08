"""A hub node's same-graph exit onto a human-judged node opens that visit's gate in the arrival's own write
(component tier)."""

from __future__ import annotations

import contextlib
from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.delivery import hub_node
from tests.support.hub_command_graphs import HUB_CMD_GRAPH_YAML, post_build_pass, submit_build_pass, to_merge_node
from tests.support.scaffolding import HubHarness, build_hub

_TO_REVIEW = HUB_CMD_GRAPH_YAML.replace(
    "description: Landed.\n          to: done", "description: Landed.\n          to: review"
)
_GATED = (
    _TO_REVIEW.rstrip("\n")
    + """
  review:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Good.
          to: done
        reject:
          description: Bad.
          to: build
"""
)


def test_a_hub_exit_onto_a_human_judged_node_opens_one_gate_and_the_chunk_waits(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=_GATED)
    submit_build_pass(hub, chunk_id, build_node_id, 1)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "waiting_on_human"
    assert detail["current_node_name"] == "review"
    decisions = hub.client.get("/api/decisions").json()["decisions"]
    assert [d["decision_id"] for d in decisions] == [detail["decision"]["decision_id"]]
    assert detail["decision"]["imposed_by_runner_id"] is None

    hub.settle_turns()
    assert len(hub.client.get("/api/decisions").json()["decisions"]) == 1, "a replayed turn opens no second gate"


def test_the_hub_exit_rolls_back_with_its_turn_when_the_gate_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=_GATED)
    before = hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_name"]

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("gate arrival refused")

    monkeypatch.setattr(hub_node, "open_graph_gate_on_arrival", _refuse)
    with contextlib.suppress(RuntimeError):
        submit_build_pass(hub, chunk_id, build_node_id, 1)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_name"] != "review", "the arrival rolled back with the failed gate open"
    assert before != "review"


_FL_TO_AFTER = HUB_CMD_GRAPH_YAML.replace(
    "description: Landed.\n          to: done", "description: Landed.\n          to: after"
)
_AFTER_RUNNER = """
  after:
    executor: runner
    prompt: After.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
"""
_AFTER_HUMAN = """
  after:
    executor: runner
    judgement:
      by: human
      choices:
        pass:
          description: Done.
          to: done
"""
_AFTER_HUB = """
  after:
    executor: hub
    action: test.answer
    with: {choice: [success]}
    judgement:
      choices:
        success:
          description: Landed.
          to: done
        failure:
          description: Failed.
          to: build
"""


def _hop(tmp_path: Path, landing: str, *, pause: bool = False) -> tuple[HubHarness, str, str]:
    """A chunk at ``merge`` of an armed v1, then a newer mint whose ``after`` is ``landing``; the hub's exit taken."""
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, v1 = to_merge_node(hub, graph_yaml=_FL_TO_AFTER + _AFTER_RUNNER)
    armed = hub.client.post(f"/api/graphs/{v1.graph_id}/follow-latest", json={"follow_latest": True})
    assert armed.status_code == 202, armed.text
    post_build_pass(hub, chunk_id, build_node_id, 1)
    hub.clock.advance(timedelta(minutes=1))
    created = hub.client.post("/api/graphs", json={"definition_yaml": _FL_TO_AFTER + landing})
    assert created.status_code == 201, created.text
    hub.settle_turns()
    return hub, chunk_id, str(created.json()["graph_id"])


def test_a_follow_latest_hop_onto_a_worker_node_releases_the_route_and_re_queues(tmp_path: Path) -> None:
    hub, chunk_id, v2 = _hop(tmp_path, _AFTER_RUNNER.replace("After.", "After, better."))

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert (detail["graph_id"], detail["current_node_name"], detail["status"]) == (v2, "after", "ready")
    assert detail["decision"] is None


def test_a_follow_latest_hop_onto_a_human_node_opens_exactly_the_landings_decision(tmp_path: Path) -> None:
    hub, chunk_id, v2 = _hop(tmp_path, _AFTER_HUMAN)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert (detail["graph_id"], detail["status"]) == (v2, "waiting_on_human")
    decisions = hub.client.get("/api/decisions").json()["decisions"]
    assert [d["decision_id"] for d in decisions] == [detail["decision"]["decision_id"]]
    hub.settle_turns()
    assert len(hub.client.get("/api/decisions").json()["decisions"]) == 1


def test_a_follow_latest_hop_onto_a_hub_node_keeps_the_route_and_the_hub_takes_it(tmp_path: Path) -> None:
    hub, chunk_id, v2 = _hop(tmp_path, _AFTER_HUB)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == v2
    assert detail["status"] == "done", "the hub ran the landed node: the route was kept, never re-queued"


def test_a_restarted_visit_is_not_hopped_by_its_stale_exit(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, v1 = to_merge_node(hub, graph_yaml=_FL_TO_AFTER + _AFTER_RUNNER)
    armed = hub.client.post(f"/api/graphs/{v1.graph_id}/follow-latest", json={"follow_latest": True})
    assert armed.status_code == 202, armed.text
    post_build_pass(hub, chunk_id, build_node_id, 1)
    hub.clock.advance(timedelta(minutes=1))
    assert (
        hub.client.post(
            "/api/graphs", json={"definition_yaml": _FL_TO_AFTER + _AFTER_RUNNER.replace("After.", "B.")}
        ).status_code
        == 201
    )
    restarted = hub.client.post(f"/api/chunks/{chunk_id}/restart", json={"node": "build"})
    assert restarted.status_code == 202, restarted.text

    hub.settle_turns()

    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    assert list(facts.migrations) == []
    assert not any(t.choice_name == "success" for t in facts.transitions)
