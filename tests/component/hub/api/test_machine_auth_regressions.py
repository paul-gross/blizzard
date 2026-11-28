"""Machine authentication across backed and store-free hub processes."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.api.auth import MachineCredentials, RunnerAuth
from blizzard.hub.process_root import HubProcess
from tests.support.scaffolding import build_hub, seed_runner
from tests.support.store_free_process import store_free


@pytest.mark.parametrize("path", ["/api/fleet/summary", "/api/fleet/host"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic invalid"}, {"Authorization": "Bearer"}])
def test_missing_credentials_require_store(tmp_path: Path, path: str, headers: dict[str, str]) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)

    backed = client.get(path, headers=headers)
    assert backed.status_code == 401, backed.text
    assert backed.json()["detail"] == "missing or malformed Authorization header"

    with store_free(hub.app):
        unavailable = client.get(path, headers=headers)
    assert unavailable.status_code == 503, unavailable.text
    assert unavailable.json()["type"] == "urn:blizzard:problem:hub-store-unavailable"


@pytest.mark.parametrize(("path", "kind"), [("/api/fleet/summary", "runner"), ("/api/fleet/host", "host")])
def test_unknown_credentials_require_store(tmp_path: Path, path: str, kind: str) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    headers = {"Authorization": "Bearer unknown-token"}

    backed = client.get(path, headers=headers)
    assert backed.status_code == 401, backed.text
    assert backed.json()["detail"] == f"bearer token does not resolve to a known {kind}"

    with store_free(hub.app):
        unavailable = client.get(path, headers=headers)
    assert unavailable.status_code == 503, unavailable.text
    assert unavailable.json()["type"] == "urn:blizzard:problem:hub-store-unavailable"


def test_trace_and_route_share_process_machine_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    token = seed_runner(hub, "identity-runner")
    process: HubProcess = hub.app.state.hub_process
    canonical = process.resolver.get(MachineCredentials)
    seen: list[MachineCredentials] = []
    original_principal = RunnerAuth.principal.fget
    assert original_principal is not None
    original_demand = RunnerAuth.demand

    def principal(auth: RunnerAuth):
        seen.append(auth.machine)
        return original_principal(auth)

    def demand(auth: RunnerAuth, *, allow_retired: bool = False):
        assert seen
        assert all(machine is canonical for machine in seen)
        seen.append(auth.machine)
        return original_demand(auth, allow_retired=allow_retired)

    monkeypatch.setattr(RunnerAuth, "principal", property(principal))
    monkeypatch.setattr(RunnerAuth, "demand", demand)
    response = TestClient(hub.app).get(
        "/api/fleet/summary",
        headers={"Authorization": f"Bearer {token}", "traceparent": f"00-{'a' * 32}-{'b' * 16}-01"},
    )
    assert response.status_code == 200, response.text
    assert len(seen) >= 2
    assert all(machine is canonical for machine in seen)
