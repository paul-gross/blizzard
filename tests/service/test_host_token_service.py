"""A host's bearer token against a real mock-hub subprocess — service tier.

The token reaches the host's own routes and nothing else, and a runner's token reaches none of them."""

from __future__ import annotations

import httpx2
import pytest

from tests.service.support import mock_hub, require_mock_fleet, service_gate
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]

_REPORT = {
    "name": "box",
    "url": "http://box.local:9000",
    "redirect_uris": ["http://box.local:9000/callback"],
    "workspaces": [{"workspace_id": "w1", "env_capacity": 2}],
    "subscriptions": [],
}
_RUNNER_TOKEN = "svc-host-runner-token"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _added_host(hub: httpx2.Client) -> dict[str, str]:
    resp = hub.post("/api/hosts", json={"name": "box"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_the_host_token_reaches_the_host_routes_and_no_runner_route() -> None:
    bin_dir = require_mock_fleet()
    with mock_hub(bin_dir, free_port()) as hub:
        host = _added_host(hub)
        reported = hub.post("/api/fleet/host", json=_REPORT, headers=_bearer(host["token"]))
        record = hub.get("/api/fleet/host", headers=_bearer(host["token"]))
        on_runner_route = hub.get("/api/fleet/identity", headers=_bearer(host["token"]))

    assert reported.status_code == 200, reported.text
    assert reported.json()["host_id"] == host["host_id"]
    assert record.status_code == 200, record.text
    assert on_runner_route.status_code == 401


def test_a_runner_token_is_refused_on_the_host_routes() -> None:
    bin_dir = require_mock_fleet()
    with mock_hub(bin_dir, free_port()) as hub:
        assert hub.post("/_seed/runners", json={"name": "r", "token": _RUNNER_TOKEN}).status_code in (200, 201)
        refused = hub.get("/api/fleet/host", headers=_bearer(_RUNNER_TOKEN))
        unknown = hub.get("/api/fleet/host", headers=_bearer("never-issued"))

    assert refused.status_code == 401
    assert unknown.status_code == 401
