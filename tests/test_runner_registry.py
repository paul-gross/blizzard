"""The fleet registry — register, heartbeat, liveness, and the pause brake (component tier).

Drives the real hub over a tmp store (D-019/D-070/D-043): ``POST /runners`` registers,
``POST /runners/{id}/heartbeats`` refreshes liveness, ``GET /runners`` lists the fleet
with **derived** online/offline and paused, and ``POST /runners/{id}/pause`` / ``/resume``
set the operator's brake. Liveness and paused are never stored columns (D-004), so the
assertions drive the clock and read the derived surface.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.registry import STALE_AFTER
from tests.support import HubHarness, build_hub, emitted_events

pytestmark = pytest.mark.component


def _register(hub: HubHarness, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> dict:
    resp = hub.client.post("/api/runners", json={"runner_id": runner_id, "workspace_id": workspace_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_register_is_idempotent_upsert(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert _register(hub)["first_registration"] is True
    again = hub.client.post("/api/runners", json={"runner_id": "runner-a", "workspace_id": "ws-b"})
    assert again.json()["first_registration"] is False
    # The re-register updated the workspace binding and refreshed last_seen.
    view = hub.client.get("/api/runners/runner-a").json()
    assert view["workspace_id"] == "ws-b"


def test_list_runners_derives_online_and_paused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    runners = hub.client.get("/api/runners").json()["runners"]
    assert len(runners) == 1
    assert runners[0]["runner_id"] == "runner-a"
    assert runners[0]["online"] is True  # just seen, at the fixed clock now
    assert runners[0]["paused"] is False


def test_liveness_goes_offline_when_stale_and_heartbeat_refreshes(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    hub.clock.advance(STALE_AFTER + timedelta(seconds=1))
    assert hub.client.get("/api/runners/runner-a").json()["online"] is False

    hb = hub.client.post("/api/runners/runner-a/heartbeats")
    assert hb.status_code == 204
    assert hub.client.get("/api/runners/runner-a").json()["online"] is True


def test_heartbeat_unknown_runner_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/runners/ghost/heartbeats").status_code == 404


def test_get_unknown_runner_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.get("/api/runners/ghost").status_code == 404


def test_pause_and_resume_flip_the_derived_brake(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)

    paused = hub.client.post("/api/runners/runner-a/pause", json={"by": "alice"})
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert hub.client.get("/api/runners/runner-a").json()["paused"] is True

    resumed = hub.client.post("/api/runners/runner-a/resume", json={"by": "alice"})
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False
    assert hub.client.get("/api/runners/runner-a").json()["paused"] is False


def test_pause_unknown_runner_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/runners/ghost/pause", json={"by": "op"}).status_code == 404


def test_registry_changes_emit_runner_changed_events(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    hub.client.post("/api/runners/runner-a/pause", json={"by": "op"})
    events = emitted_events(hub)
    assert [e["event"] for e in events] == ["runner-changed", "runner-changed"]
    assert all("runner-a" in e["data"] for e in events)
