"""``GET /api/runners/{runner_id}/next-claim`` — one runner's next claim, explained (component tier).

The route applies no claim bar: a paused or retired runner is answered like any other. Only an unknown id is refused."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.scaffolding import HubHarness, build_hub

_CAPABILITIES = [{"harness_id": "claude", "default": True}]


def _register(hub: HubHarness, runner_id: str = "r1") -> None:
    resp = hub.client.post(
        "/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": "w1", "capabilities": _CAPABILITIES}
    )
    assert resp.status_code == 201, resp.text


def _ready(hub: HubHarness, ref: str) -> str:
    resp = hub.client.post("/api/chunks", json={"tokens": [f"default:{ref}"]})
    assert resp.status_code == 201, resp.text
    chunk_id = str(resp.json()["chunk_id"])
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


def _blocked_queue(hub: HubHarness) -> tuple[str, str]:
    dependent, prerequisite = _ready(hub, "1"), _ready(hub, "2")
    declared = hub.client.post(f"/api/chunks/{dependent}/dependencies", json={"prerequisite_chunk_id": prerequisite})
    assert declared.status_code == 202, declared.text
    return dependent, prerequisite


@pytest.mark.parametrize("lifecycle", ["pause", "retire"])
def test_a_paused_or_retired_runner_still_has_its_next_claim_explained(tmp_path: Path, lifecycle: str) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    dependent, prerequisite = _blocked_queue(hub)
    before = hub.client.get("/api/runners/r1/next-claim")
    assert before.status_code == 200, before.text
    assert hub.client.post(f"/api/runners/r1/{lifecycle}", json={"by": "op"}).status_code == 200

    resp = hub.client.get("/api/runners/r1/next-claim")
    assert resp.status_code == 200, resp.text
    assert resp.json() == before.json()
    body = resp.json()
    assert body["next"]["chunk_id"] == prerequisite
    assert [(e["chunk_id"], e["reason"], e["prerequisite"], e["prerequisite_count"]) for e in body["passed"]] == [
        (dependent, "blocked", prerequisite, 1)
    ]
    assert body["passed_total"] == 1
    assert body["if_holding"] is None
    assert [e["chunk_id"] for e in body["held_at"]] == [dependent]


def test_an_unknown_runner_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/runners/does-not-exist/next-claim")
    assert resp.status_code == 404
    assert resp.json()["type"] == "urn:blizzard:problem:runner-not-found"
