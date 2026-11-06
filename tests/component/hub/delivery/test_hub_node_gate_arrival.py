"""A hub node's same-graph exit onto a human-judged node opens that visit's gate in the arrival's own write
(component tier)."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from blizzard.hub.delivery import hub_node
from tests.support.hub_command_graphs import HUB_CMD_GRAPH_YAML, submit_build_pass, to_merge_node
from tests.support.scaffolding import build_hub

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
