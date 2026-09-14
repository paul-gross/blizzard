"""``ChunkStatusView`` pins its fields equal to the same chunk's ``ChunkDetail`` fields —
component tier (blizzard#521). Drives a chunk through running, paused, restarted, an
open gate decision, a resolved-but-not-transitioned decision, and recorded usage/cost,
asserting the slim batch read and the full aggregate agree on every shared field."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, ingest, report_lease

pytestmark = pytest.mark.component

_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build it.
    judgement:
      prompt: Assess the build.
      choices:
        pass:
          description: Complete.
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
          description: Ship it.
          to: done
        reject:
          description: Send back.
          to: build
"""


def _build_node_id(hub) -> str:  # type: ignore[no-untyped-def]
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
    assert graph.status_code == 201, graph.text
    return next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")


def _claim(hub, chunk_id: str, *, runner_id: str = "r1", epoch: int = 1) -> None:  # type: ignore[no-untyped-def]
    claimed = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    assert claimed.status_code == 201, claimed.text
    report_lease(hub, chunk_id, epoch=epoch, seq=epoch, runner_id=runner_id)


def _status_for(hub, chunk_id: str) -> dict:  # type: ignore[no-untyped-def]
    resp = hub.client.get("/api/fleet/chunk-statuses", params={"chunk_id": [chunk_id]})
    assert resp.status_code == 200, resp.text
    [view] = resp.json()
    return view


def _detail_for(hub, chunk_id: str) -> dict:  # type: ignore[no-untyped-def]
    resp = hub.client.get(f"/api/chunks/{chunk_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assert_parity(hub, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    status_view = _status_for(hub, chunk_id)
    detail = _detail_for(hub, chunk_id)

    assert status_view["status"] == detail["status"]
    assert status_view["route_runner_id"] == (detail["route"]["runner_id"] if detail["route"] else None)
    assert status_view["pause"] == detail["pause"]
    assert status_view["latest_epoch"] == detail["latest_epoch"]
    assert status_view["restart_epochs"] == [r["epoch"] for r in detail["restarts"]]
    assert status_view["cost"] == detail["cost"]
    if detail["decision"] is None:
        assert status_view["decision"] is None
    else:
        assert status_view["decision"] is not None
        for field in ("decision_id", "node_id", "epoch", "resolved_choice", "transitioned"):
            assert status_view["decision"][field] == detail["decision"][field], field


def test_running_chunk_field_parity(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _build_node_id(hub)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    _claim(hub, chunk_id)

    _assert_parity(hub, chunk_id)


def test_paused_chunk_field_parity(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _build_node_id(hub)
    chunk_id = ingest(hub, [{"source": "default", "ref": "2"}])
    _claim(hub, chunk_id)
    assert hub.client.post(f"/api/fleet/chunks/{chunk_id}/pause").status_code == 202

    _assert_parity(hub, chunk_id)


def test_restarted_chunk_field_parity(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _build_node_id(hub)
    chunk_id = ingest(hub, [{"source": "default", "ref": "3"}])
    _claim(hub, chunk_id)
    assert hub.client.post(f"/api/chunks/{chunk_id}/restart", json={}).status_code == 202

    _assert_parity(hub, chunk_id)


def test_decision_waiting_chunk_field_parity(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    build_node_id = _build_node_id(hub)
    chunk_id = ingest(hub, [{"source": "default", "ref": "4"}])
    _claim(hub, chunk_id)
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build_node_id, "artifacts": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "parked_at_gate"

    _assert_parity(hub, chunk_id)


def test_decision_resolved_not_transitioned_chunk_field_parity(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    build_node_id = _build_node_id(hub)
    chunk_id = ingest(hub, [{"source": "default", "ref": "5"}])
    _claim(hub, chunk_id)
    hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build_node_id, "artifacts": []},
    )
    decision_id = _detail_for(hub, chunk_id)["decision"]["decision_id"]
    resolved = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"})
    assert resolved.status_code == 200, resolved.text

    detail = _detail_for(hub, chunk_id)
    assert detail["decision"]["resolved_choice"] == "approve"
    assert detail["decision"]["transitioned"] is False
    _assert_parity(hub, chunk_id)


def test_decision_closed_by_restart_chunk_field_parity(tmp_path: Path) -> None:
    """The decision-closure check has four arms (transitions, migrations, escalations,
    restarts — issue #370); every other decision test here closes one via a transition.
    This one closes it via a restart instead, so the shared closure rule
    (`ChunkDecisionsStore._decision_closure_ids`) is proven on a second arm, not just the
    one every sibling test happens to exercise."""
    hub = build_hub(tmp_path)
    build_node_id = _build_node_id(hub)
    chunk_id = ingest(hub, [{"source": "default", "ref": "7"}])
    _claim(hub, chunk_id)
    parked = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build_node_id, "artifacts": []},
    )
    assert parked.status_code == 200, parked.text
    assert parked.json()["outcome"] == "parked_at_gate"

    restarted = hub.client.post(f"/api/chunks/{chunk_id}/restart", json={"node": "build"})
    assert restarted.status_code == 202, restarted.text

    detail = _detail_for(hub, chunk_id)
    assert detail["decision"] is None  # closed by the restart, not a transition
    assert detail["restarts"][0]["decision_id"] is not None
    _assert_parity(hub, chunk_id)


def test_usage_cost_chunk_field_parity(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    build_node_id = _build_node_id(hub)
    chunk_id = ingest(hub, [{"source": "default", "ref": "6"}])
    _claim(hub, chunk_id)
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "usage.recorded",
                    "payload": {
                        "chunk_id": chunk_id,
                        "node_id": build_node_id,
                        "epoch": 1,
                        "kind": "spawn",
                        "model": "m",
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_tokens": 1,
                        "cache_create_tokens": 1,
                        "cost_usd": 0.25,
                    },
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    detail = _detail_for(hub, chunk_id)
    assert detail["cost"]["cost_usd"] == pytest.approx(0.25)
    _assert_parity(hub, chunk_id)
