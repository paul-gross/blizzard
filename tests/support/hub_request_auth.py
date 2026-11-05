"""Helpers for the fleet auth tests, shared with other test modules."""

from __future__ import annotations

from pathlib import Path

from tests.support.scaffolding import HubHarness, build_hub, seed_runner


def register_runner(hub: HubHarness, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> None:
    resp = hub.client.post("/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": workspace_id})
    assert resp.status_code == 201, resp.text


def enroll_runner(hub: HubHarness, runner_id: str = "runner-a") -> str:
    resp = hub.client.post(f"/api/runners/{runner_id}/enrollments")
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


def seed_enrolled(tmp_path: Path, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> str:
    """Add and register ``runner_id`` in the store over ``tmp_path``; return its token. The caller
    builds its own hub over the same store, as a restarted hub reopens it."""
    return seed_runner(build_hub(tmp_path), runner_id, workspace_id=workspace_id)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def ingest_chunk(hub: HubHarness, headers: dict[str, str] | None = None, *, pointer_token: str = "default:1") -> str:
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token]}, headers=headers)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["chunk_id"])


def enrolled_runner_token(tmp_path: Path) -> str:
    """A runner token enrolled through its own throwaway hub over ``tmp_path``, for the caller's
    oauth hub to read — the same two-hub-instances-over-one-store shape ``tests/component/hub/api/test_fleet_auth_partition.py``'s
    own ``_seed_enrolled`` uses."""
    seed_hub = build_hub(tmp_path)
    register_runner(seed_hub, runner_id="runner-a", workspace_id="ws-a")
    return enroll_runner(seed_hub, "runner-a")
