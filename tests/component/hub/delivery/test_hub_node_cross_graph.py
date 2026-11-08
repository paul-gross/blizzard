"""A hub node's turn that ends on a ``graph:<name>`` choice migrates the chunk (component tier).

The migration, the re-pin and the turn's end are one transaction; a target naming no enabled graph
escalates ``migration-target-unresolvable`` and never ``unroutable-outcome``; a standing intent stays
parked across an ordinary hub exit.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.delivery import hub_node
from tests.support.hub_command_graphs import (
    ALWAYS_PENDING_GRAPH_YAML,
    HUB_CMD_GRAPH_YAML,
    post_build_pass,
    submit_build_pass,
    to_merge_node,
)
from tests.support.scaffolding import HubHarness, build_hub

_CROSS = HUB_CMD_GRAPH_YAML.replace(
    "description: Landed.\n          to: done", "description: Landed.\n          to: graph:@TARGET@"
)

_RUNNER_TARGET_YAML = """
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


def _drive(hub: HubHarness, *, target: str, mint_target: bool) -> str:
    if mint_target:
        assert hub.client.post("/api/graphs", json={"definition_yaml": _RUNNER_TARGET_YAML}).status_code == 201
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=_CROSS.replace("@TARGET@", target))
    submit_build_pass(hub, chunk_id, build_node_id, 1)
    return chunk_id


def test_a_hub_nodes_cross_graph_choice_migrates_and_re_queues(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _drive(hub, target="triage", mint_target=True)

    triage_id = next(g["graph_id"] for g in hub.client.get("/api/graphs").json() if g["name"] == "triage")
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == triage_id
    assert detail["current_node_name"] == "build"
    assert detail["status"] == "ready"


def test_an_unresolvable_cross_graph_target_escalates_as_a_migration_target(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _drive(hub, target="absent", mint_target=False)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "needs_human"
    assert "migration-target-unresolvable" in str(detail)
    assert "unroutable-outcome" not in str(detail)
    assert not any(a["name"] == "hub-unroutable-outcome" for a in detail["artifacts"])


def _retire(hub: HubHarness, graph_id: str) -> None:
    retire = hub.client.post(f"/api/graphs/{graph_id}/retire", json={"by": "operator"})
    assert retire.status_code == 202, retire.text


def _mint_triage(hub: HubHarness, *, prompt: str) -> str:
    created = hub.client.post("/api/graphs", json={"definition_yaml": _RUNNER_TARGET_YAML.replace("Triage.", prompt)})
    assert created.status_code == 201, created.text
    return created.json()["graph_id"]


def _cross_chunk(hub: HubHarness) -> str:
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=_CROSS.replace("@TARGET@", "triage"))
    submit_build_pass(hub, chunk_id, build_node_id, 1)
    return chunk_id


def test_a_cross_graph_target_whose_only_mint_is_retired_escalates_as_a_migration_target(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _retire(hub, _mint_triage(hub, prompt="Triage."))

    chunk_id = _cross_chunk(hub)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "needs_human"
    assert detail["escalation"]["cause"] == "migration-target-unresolvable"


def test_a_cross_graph_choice_migrates_onto_the_older_mint_when_the_newest_is_retired(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    older = _mint_triage(hub, prompt="Triage first.")
    hub.clock.advance(timedelta(hours=1))
    newest = _mint_triage(hub, prompt="Triage second.")
    _retire(hub, newest)

    chunk_id = _cross_chunk(hub)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == older
    assert detail["status"] == "ready"


def test_a_standing_intent_stays_parked_across_an_ordinary_hub_exit(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _RUNNER_TARGET_YAML}).status_code == 201
    chunk_id, build_node_id, _graph = to_merge_node(hub)
    patched = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": "triage"}})
    assert patched.status_code == 202, patched.text
    before = hub.client.get(f"/api/chunks/{chunk_id}").json()["intended_migration"]
    assert before is not None

    submit_build_pass(hub, chunk_id, build_node_id, 1)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    assert detail["intended_migration"] == before


_HUB_TARGET_YAML = HUB_CMD_GRAPH_YAML.replace("name: default-delivery", "name: triage")

_GATE_TARGET_YAML = _RUNNER_TARGET_YAML.replace(
    "    prompt: Triage.\n    judgement:\n      prompt: Assess.",
    "    judgement:\n      by: human",
)


def test_a_cross_graph_choice_onto_a_hub_node_retains_the_route_and_the_hub_takes_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _HUB_TARGET_YAML}).status_code == 201
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=_CROSS.replace("@TARGET@", "triage"))
    submit_build_pass(hub, chunk_id, build_node_id, 1)

    triage_id = next(g["graph_id"] for g in hub.client.get("/api/graphs").json() if g["name"] == "triage")
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == triage_id
    # Landed on triage's same-named hub node `merge`, which the hub then ran to the end: never re-queued.
    assert detail["status"] == "done"


def test_the_migration_rolls_back_with_its_turn_when_a_later_write_in_the_transaction_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GATE_TARGET_YAML}).status_code == 201
    chunk_id, build_node_id, graph = to_merge_node(hub, graph_yaml=_CROSS.replace("@TARGET@", "triage"))

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("gate arrival refused")

    monkeypatch.setattr(hub_node, "open_graph_gate_on_arrival", _refuse)
    with contextlib.suppress(RuntimeError):
        submit_build_pass(hub, chunk_id, build_node_id, 1)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == graph.graph_id, "the migration rolled back with the failed transaction"


def test_a_poll_timeout_whose_failure_edge_is_cross_graph_migrates(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _RUNNER_TARGET_YAML}).status_code == 201
    graph_yaml = ALWAYS_PENDING_GRAPH_YAML.replace(
        "description: Red.\n          to: build", "description: Red.\n          to: graph:triage"
    )
    chunk_id, build_node_id, _graph = to_merge_node(hub, graph_yaml=graph_yaml)
    submit_build_pass(hub, chunk_id, build_node_id, 1)
    for _ in range(3):
        hub.clock.advance(timedelta(seconds=31))
        hub.turn_outcome(chunk_id)

    triage_id = next(g["graph_id"] for g in hub.client.get("/api/graphs").json() if g["name"] == "triage")
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["graph_id"] == triage_id
    assert detail["pending"] is None


# --- follow-latest at an ordinary nonterminal hub exit -------------------------------------------------------

_AFTER_NODE = """
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
_FL_YAML = (
    HUB_CMD_GRAPH_YAML.replace("description: Landed.\n          to: done", "description: Landed.\n          to: after")
    + _AFTER_NODE
)
_FL_HUMAN_YAML = _FL_YAML.replace(
    "    prompt: After.\n    judgement:\n      prompt: Assess.", "    judgement:\n      by: human"
)
_FL_NO_AFTER_YAML = HUB_CMD_GRAPH_YAML.replace(
    "description: Landed.\n          to: done", "description: Landed.\n          to: build"
)


def _mint_graph(hub: HubHarness, yaml_text: str) -> str:
    created = hub.client.post("/api/graphs", json={"definition_yaml": yaml_text})
    assert created.status_code == 201, created.text
    return str(created.json()["graph_id"])


def _policy(hub: HubHarness, graph_id: str, value: bool | None) -> None:
    resp = hub.client.post(f"/api/graphs/{graph_id}/follow-latest", json={"follow_latest": value})
    assert resp.status_code == 202, resp.text


def _park_then_mint(
    hub: HubHarness, *, v1_yaml: str, v2_yaml: str | None, policy: bool | None = True
) -> tuple[str, str | None]:
    """A chunk standing at hub node ``merge`` of v1, the hub's pass not yet taken, with a newer mint of v2_yaml."""
    chunk_id, build_node_id, graph = to_merge_node(hub, graph_yaml=v1_yaml)
    _policy(hub, graph.graph_id, policy)
    post_build_pass(hub, chunk_id, build_node_id, 1)
    v2 = None
    if v2_yaml is not None:
        hub.clock.advance(timedelta(minutes=1))
        v2 = _mint_graph(hub, v2_yaml)
    return chunk_id, v2


def _detail(hub: HubHarness, chunk_id: str) -> dict[str, Any]:
    return dict(hub.client.get(f"/api/chunks/{chunk_id}").json())


def _migrations(hub: HubHarness, chunk_id: str) -> list[object]:
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    return list(facts.migrations)


def test_a_hub_exit_on_a_follow_latest_pin_migrates_onto_the_newer_mint_instead_of_transitioning(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path)
    chunk_id, v2 = _park_then_mint(hub, v1_yaml=_FL_YAML, v2_yaml=_FL_YAML.replace("Build the change.", "Better."))

    hub.settle_turns()

    detail = _detail(hub, chunk_id)
    assert detail["graph_id"] == v2
    assert detail["current_node_name"] == "after"
    assert detail["status"] == "ready"
    facts = hub.services.chunks.facts.load_facts(chunk_id)
    assert facts is not None
    assert [m.source.value if m.source else None for m in facts.migrations] == ["follow-latest"]
    assert not any(t.from_node_id == facts.migrations[0].from_node_id for t in facts.transitions)


def test_a_hub_exit_lands_on_the_newer_mints_entry_when_it_lacks_the_destination(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, v2 = _park_then_mint(hub, v1_yaml=_FL_YAML, v2_yaml=_FL_NO_AFTER_YAML)

    hub.settle_turns()

    detail = _detail(hub, chunk_id)
    assert detail["graph_id"] == v2
    assert detail["current_node_name"] == "build"
    assert detail["status"] == "ready"


def test_a_hub_exit_into_a_human_judged_destination_opens_that_landings_decision(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, v2 = _park_then_mint(hub, v1_yaml=_FL_YAML, v2_yaml=_FL_HUMAN_YAML)

    hub.settle_turns()

    detail = _detail(hub, chunk_id)
    assert detail["graph_id"] == v2
    assert detail["current_node_name"] == "after"
    assert detail["status"] == "waiting_on_human"


@pytest.mark.parametrize("policy", [None, False])
def test_a_hub_exit_without_the_policy_transitions_as_before(tmp_path: Path, policy: bool | None) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _v2 = _park_then_mint(
        hub, v1_yaml=_FL_YAML, v2_yaml=_FL_YAML.replace("Build the change.", "Better."), policy=policy
    )

    hub.settle_turns()

    detail = _detail(hub, chunk_id)
    assert detail["current_node_name"] == "after"
    assert _migrations(hub, chunk_id) == []


def test_the_fleet_default_turns_the_policy_on_for_an_inheriting_graph(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, follow_latest=True)
    chunk_id, v2 = _park_then_mint(
        hub, v1_yaml=_FL_YAML, v2_yaml=_FL_YAML.replace("Build the change.", "Better."), policy=None
    )

    hub.settle_turns()

    assert _detail(hub, chunk_id)["graph_id"] == v2
    assert len(_migrations(hub, chunk_id)) == 1


def test_a_hub_exit_with_no_newer_mint_transitions(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _park_then_mint(hub, v1_yaml=_FL_YAML, v2_yaml=None)

    hub.settle_turns()

    assert _detail(hub, chunk_id)["current_node_name"] == "after"
    assert _migrations(hub, chunk_id) == []


def test_a_hub_exit_finishing_at_done_does_not_migrate(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _park_then_mint(
        hub, v1_yaml=HUB_CMD_GRAPH_YAML, v2_yaml=HUB_CMD_GRAPH_YAML.replace("Build the change.", "Better.")
    )

    hub.settle_turns()

    assert _detail(hub, chunk_id)["status"] == "done"
    assert _migrations(hub, chunk_id) == []


def test_a_standing_intent_suppresses_the_follow_latest_hop(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _RUNNER_TARGET_YAML}).status_code == 201
    chunk_id, _v2 = _park_then_mint(hub, v1_yaml=_FL_YAML, v2_yaml=_FL_YAML.replace("Build the change.", "Better."))
    patched = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": "triage"}})
    assert patched.status_code == 202, patched.text

    hub.settle_turns()

    assert _migrations(hub, chunk_id) == []
    assert _detail(hub, chunk_id)["current_node_name"] == "after"


def test_a_failure_exit_on_a_follow_latest_pin_migrates(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_yaml = _FL_YAML.replace("with: {choice: [success]}", "with: {choice: [failure]}").replace(
        "description: Failed to land.\n          to: build", "description: Failed to land.\n          to: after"
    )
    chunk_id, v2 = _park_then_mint(hub, v1_yaml=graph_yaml, v2_yaml=graph_yaml.replace("Build the change.", "Better."))

    hub.settle_turns()

    assert _detail(hub, chunk_id)["graph_id"] == v2
    assert len(_migrations(hub, chunk_id)) == 1


def test_a_poll_timeout_exit_on_a_follow_latest_pin_migrates_and_keeps_its_bounce(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_yaml = ALWAYS_PENDING_GRAPH_YAML.replace(
        "description: Red.\n          to: build", "description: Red.\n          to: after"
    ).replace("  merge:", _AFTER_NODE.strip("\n") + "\n  merge:")
    chunk_id, build_node_id, graph = to_merge_node(hub, graph_yaml=graph_yaml)
    _policy(hub, graph.graph_id, True)
    submit_build_pass(hub, chunk_id, build_node_id, 1)
    hub.clock.advance(timedelta(minutes=1))
    v2 = _mint_graph(hub, graph_yaml.replace("Build the change.", "Better."))
    for _ in range(3):
        hub.clock.advance(timedelta(seconds=31))
        hub.turn_outcome(chunk_id)

    detail = _detail(hub, chunk_id)
    assert detail["graph_id"] == v2
    assert len(_migrations(hub, chunk_id)) == 1
    assert any(a["name"] == "bounce-envelope" for a in detail["artifacts"])


def test_the_follow_latest_hop_rolls_back_with_its_turn_when_the_landed_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _v2 = _park_then_mint(hub, v1_yaml=_FL_YAML, v2_yaml=_FL_HUMAN_YAML)
    graph_id = _detail(hub, chunk_id)["graph_id"]

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("gate arrival refused")

    monkeypatch.setattr(hub_node, "open_graph_gate_on_arrival", _refuse)
    with contextlib.suppress(RuntimeError):
        hub.settle_turns()

    assert _detail(hub, chunk_id)["graph_id"] == graph_id
    assert _migrations(hub, chunk_id) == []


def test_a_chunk_paused_at_the_hub_node_is_not_hopped_by_the_exit(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, v1 = to_merge_node(hub, graph_yaml=_FL_YAML)
    _policy(hub, v1.graph_id, True)
    assert hub.client.post(f"/api/chunks/{chunk_id}/pause", json={}).status_code == 202
    post_build_pass(hub, chunk_id, build_node_id, 1)
    hub.clock.advance(timedelta(minutes=1))
    _mint_graph(hub, _FL_YAML.replace("Build the change.", "Better."))

    hub.settle_turns()

    assert _migrations(hub, chunk_id) == []
    assert _detail(hub, chunk_id)["status"] == "paused"


def test_a_replayed_hub_exit_writes_one_migration_and_nothing_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    chunk_id, v2 = _park_then_mint(hub, v1_yaml=_FL_YAML, v2_yaml=_FL_YAML.replace("Build the change.", "Better."))
    original = hub_node.HubNodeRouter.route

    def _twice(self: hub_node.HubNodeRouter, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        original(self, *args, **kwargs)

    monkeypatch.setattr(hub_node.HubNodeRouter, "route", _twice)

    hub.settle_turns()

    assert _detail(hub, chunk_id)["graph_id"] == v2
    assert len(_migrations(hub, chunk_id)) == 1


def test_an_intent_set_between_the_policy_read_and_the_lock_suppresses_the_hop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _RUNNER_TARGET_YAML}).status_code == 201
    chunk_id, _v2 = _park_then_mint(hub, v1_yaml=_FL_YAML, v2_yaml=_FL_YAML.replace("Build the change.", "Better."))
    original = hub_node.HubNodeRouter._drift  # pyright: ignore[reportPrivateUsage]

    def _then_intend(self: hub_node.HubNodeRouter, *args: Any, **kwargs: Any) -> Any:
        read = original(self, *args, **kwargs)
        patched = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": "triage"}})
        assert patched.status_code == 202, patched.text
        return read

    monkeypatch.setattr(hub_node.HubNodeRouter, "_drift", _then_intend)

    hub.settle_turns()

    assert _migrations(hub, chunk_id) == []
    assert _detail(hub, chunk_id)["current_node_name"] == "after"


def test_a_retired_current_pin_still_drifts_but_a_retired_newest_mint_is_not_a_target(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, build_node_id, v1 = to_merge_node(hub, graph_yaml=_FL_YAML)
    _policy(hub, v1.graph_id, True)
    post_build_pass(hub, chunk_id, build_node_id, 1)
    hub.clock.advance(timedelta(minutes=1))
    v2 = _mint_graph(hub, _FL_YAML.replace("Build the change.", "Better."))
    hub.clock.advance(timedelta(minutes=1))
    v3 = _mint_graph(hub, _FL_YAML.replace("Build the change.", "Best."))
    assert hub.client.post(f"/api/graphs/{v3}/retire", json={"by": "operator"}).status_code == 202
    assert hub.client.post(f"/api/graphs/{v1.graph_id}/retire", json={"by": "operator"}).status_code == 202

    hub.settle_turns()

    assert _detail(hub, chunk_id)["graph_id"] == v2


def test_a_later_exit_reads_the_policy_of_the_pin_it_landed_on(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, v2 = _park_then_mint(hub, v1_yaml=_FL_YAML, v2_yaml=_FL_YAML.replace("Build the change.", "Better."))
    hub.settle_turns()
    assert _detail(hub, chunk_id)["graph_id"] == v2
    hub.clock.advance(timedelta(minutes=1))
    _mint_graph(hub, _FL_YAML.replace("Build the change.", "Best."))

    # v2 inherits the default (off): nothing follows from it, though v1 was armed.
    assert len(_migrations(hub, chunk_id)) == 1
