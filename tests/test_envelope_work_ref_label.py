"""The work-ref label rides the envelopes the hub renders on its own — the apply-advance
envelope a completion returns, and the fleet GET envelope re-read (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, ingest, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}
_LABELLED = [{**_POINTER, "label": "default#7"}]

_YAML = """
name: default-delivery
entry: plan
nodes:
  plan:
    executor: runner
    prompt: Plan it.
    judgement:
      prompt: Assess the plan.
      choices:
        pass:
          description: Planned.
          to: build
  build:
    executor: runner
    prompt: Build it.
    judgement:
      prompt: Assess the build.
      choices:
        pass:
          description: Built.
          to: done
"""


def _claimed(hub) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    chunk_id = ingest(hub, [_POINTER])
    claimed = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert claimed.status_code == 201, claimed.text
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return chunk_id, claimed.json()["envelope"]["node"]["node_id"]


def test_the_apply_advance_envelope_labels_each_work_ref(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, plan_node = _claimed(hub)

    advanced = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": plan_node, "artifacts": []},
    )

    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["outcome"] == "next"
    assert advanced.json()["next_envelope"]["work_refs"] == _LABELLED


def test_the_fleet_get_envelope_labels_each_work_ref(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _claimed(hub)

    envelope = hub.client.get(f"/api/fleet/chunks/{chunk_id}/envelope")

    assert envelope.status_code == 200, envelope.text
    assert envelope.json()["work_refs"] == _LABELLED
