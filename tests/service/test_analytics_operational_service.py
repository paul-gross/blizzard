"""The analytics operational-datasets routes against a real hub (blizzard#256, Phase 5):
durations, spend, and outcomes each reflect a real completed step over the packaged
default graph's own entry node. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.test_acceptance_loop import REPO, REPO_NAME, _forge, _free_port, _hub
from tests.service.support import mint_fixture, require_mock_fleet, require_winter_source, service_gate

pytestmark = [pytest.mark.service, service_gate]


def _stack(tmp_path: Path):  # type: ignore[no-untyped-def]
    bin_dir = require_mock_fleet()
    _workspace, origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    forge_port, hub_port = _free_port(), _free_port()
    return bin_dir, origins, forge_port, hub_port


def _ingest(forge, hub, title: str) -> str:  # type: ignore[no-untyped-def]
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "the chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    return str(ingested.json()["chunk_id"])


def _entry_node_id(hub, chunk_id: str) -> str:  # type: ignore[no-untyped-def]
    detail = hub.get(f"/api/chunks/{chunk_id}")
    assert detail.status_code == 200, detail.text
    node_id = detail.json()["current_node_id"]
    assert node_id is not None
    return str(node_id)


def _push_usage(hub, *, chunk_id: str, node_id: str, epoch: int, seq: int) -> None:  # type: ignore[no-untyped-def]
    payload = {
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": epoch,
        "kind": "spawn",
        "model": "claude-opus-4-8",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 10,
        "cache_create_tokens": 5,
        "cost_usd": 0.1,
    }
    resp = hub.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": seq, "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text


def test_the_three_datasets_reflect_a_real_completed_step_over_a_live_hub(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "analytics operational datasets over a real hub")
        node_id = _entry_node_id(hub, chunk_id)

        lease = hub.post(
            "/api/fleet/events",
            json={
                "runner_id": "r1",
                "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
            },
        )
        assert lease.status_code == 200, lease.text
        _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2)
        completion = hub.post(
            f"/api/fleet/chunks/{chunk_id}/completions",
            # `already-done` closes the packaged default graph's `triage` entry node
            # without entering a lane — no worktree needed for this shape assertion.
            json={"choice": "already-done", "epoch": 1, "runner_id": "r1", "from_node_id": node_id},
        )
        assert completion.status_code == 200, completion.text

        durations = hub.get("/api/analytics/durations/nodes")
        assert durations.status_code == 200, durations.text
        by_node = {row["key"]: row for row in durations.json()["durations"]}
        assert by_node[node_id]["completed_steps"] == 1

        spend = hub.get("/api/analytics/spend/nodes")
        assert spend.status_code == 200, spend.text
        spend_by_node = {row["key"]: row for row in spend.json()["spend"]}
        assert spend_by_node[node_id]["input_tokens"] == 100
        assert spend_by_node[node_id]["cost_partial"] is False

        outcomes = hub.get("/api/analytics/outcomes/nodes")
        assert outcomes.status_code == 200, outcomes.text
        outcomes_by_node = {row["node_id"]: row for row in outcomes.json()["outcomes"]}
        assert outcomes_by_node[node_id]["choice_counts"] == {"already-done": 1}
        assert outcomes_by_node[node_id]["attempt_failures"] == 0
