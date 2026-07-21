"""Operator ``GET /api/runners/{runner_id}`` (issue #104, S5), component tier.

Symmetric with ``GET /api/runners``: reuses :func:`~blizzard.hub.api.runners.runner_view`
for the same derived-liveness shape, 404 on unknown, and rejects a runner's bearer
token like every other verb on this router. This is the operator router's own detail
read — distinct from the runner-authenticated ``GET /api/fleet/runners/{id}`` (the
runner's own pull read), which coexists at a different prefix and is untouched here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component


def _register(hub, runner_id: str = "r1", workspace_id: str = "w1") -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": workspace_id})
    assert resp.status_code == 201, resp.text


def test_get_runner_returns_the_same_view_the_list_carries(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)

    resp = hub.client.get("/api/runners/r1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["runner_id"] == "r1"
    assert resp.json()["workspace_id"] == "w1"

    listed = hub.client.get("/api/runners").json()["runners"]
    assert resp.json() == next(r for r in listed if r["runner_id"] == "r1")


def test_get_runner_unknown_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/runners/does-not-exist")
    assert resp.status_code == 404


def test_get_runner_reflects_pause_state(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    assert hub.client.post("/api/runners/r1/pause", json={"by": "op"}).status_code == 200

    resp = hub.client.get("/api/runners/r1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["hub_paused"] is True


def test_runner_bearer_token_is_rejected_on_get_runner(tmp_path: Path) -> None:
    from blizzard.hub.config import RUNNER_AUTH_ENFORCE
    from tests.test_fleet_auth import _bearer, _seed_enrolled

    token = _seed_enrolled(tmp_path, runner_id="r1", workspace_id="w1")
    hub = build_hub(tmp_path, runner_auth_mode=RUNNER_AUTH_ENFORCE)

    resp = hub.client.get("/api/runners/r1", headers=_bearer(token))
    assert resp.status_code == 403
