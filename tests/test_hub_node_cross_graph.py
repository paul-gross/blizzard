"""A hub node's turn that ends on a ``graph:<name>`` choice migrates the chunk (component tier).

The migration, the re-pin and the turn's end are one transaction; a target naming no enabled graph
escalates ``migration-target-unresolvable`` and never ``unroutable-outcome``; a standing intent stays
parked across an ordinary hub exit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub
from tests.test_hub_command_node import _HUB_CMD_GRAPH_YAML, _submit_build_pass, _to_merge_node

pytestmark = pytest.mark.component

_CROSS = _HUB_CMD_GRAPH_YAML.replace(
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


def _drive(hub, *, target: str, mint_target: bool):  # type: ignore[no-untyped-def]
    if mint_target:
        assert hub.client.post("/api/graphs", json={"definition_yaml": _RUNNER_TARGET_YAML}).status_code == 201
    chunk_id, build_node_id, _graph = _to_merge_node(hub, graph_yaml=_CROSS.replace("@TARGET@", target))
    _submit_build_pass(hub, chunk_id, build_node_id, 1)
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


def test_a_standing_intent_stays_parked_across_an_ordinary_hub_exit(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _RUNNER_TARGET_YAML}).status_code == 201
    chunk_id, build_node_id, _graph = _to_merge_node(hub)
    patched = hub.client.patch(f"/api/chunks/{chunk_id}", json={"intended_migration": {"to_graph": "triage"}})
    assert patched.status_code == 202, patched.text
    before = hub.client.get(f"/api/chunks/{chunk_id}").json()["intended_migration"]
    assert before is not None

    _submit_build_pass(hub, chunk_id, build_node_id, 1)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "done"
    assert detail["intended_migration"] == before
