"""Aggregate identity and runner registration problem responses."""

from pathlib import Path

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.api.auth import RunnerAuth, RunnerPrincipal, require_runner_principal
from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant
from blizzard.hub.api.fleet import FleetRequest
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.domain.runners.version_window import VersionWindow
from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    AuthenticationRevokedProblem,
    AuthenticationUnresolvedProblem,
)
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.runner import (
    RunnerIdentityProblem,
    RunnerNotFoundProblem,
    RunnerNotRegisteredProblem,
    RunnerOwnershipProblem,
    RunnerRetiredProblem,
    RunnerVersionProblem,
)
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.credential_runners import seed_credential_runners
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant


def test_identity_problem_variants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    _, revoked, retired = seed_credential_runners(hub)
    path = "/api/fleet/identity"
    observations: list[RefusalObservation] = []
    for headers, reason in (
        ({}, "missing"),
        (bearer("invalid"), "unknown"),
        (bearer(revoked), "revoked"),
        (bearer(retired), "retired"),
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 401, response.text
        assert response.json()["reason"] == reason
        observations.append(
            RefusalObservation(
                "get", path, 401, response.headers["content-type"], RunnerIdentityProblem, response.json()
            )
        )
    hub.app.state.services = None
    response = client.get(path)
    assert response.status_code == 503, response.text
    observations.append(
        RefusalObservation(
            "get", path, 503, response.headers["content-type"], HubStoreUnavailableProblem, response.json()
        )
    )
    assert coverage_mismatches(hub.app.openapi(), {("get", path)}, observations) == []


@pytest.mark.parametrize("kind", ["registration", "read", "decisions"])
def test_runner_registration_and_reads_problem_variants(
    tmp_path: Path, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path, hub_version="0.3", version_window=VersionWindow(floor="0.2"))
    assert hub.app is not None
    client = TestClient(hub.app)
    live, revoked, retired = seed_credential_runners(hub, register_live=False)
    method, path = {
        "registration": ("post", "/api/fleet/runners"),
        "read": ("get", "/api/fleet/runners/{runner_id}"),
        "decisions": ("get", "/api/fleet/runners/{runner_id}/decisions"),
    }[kind]
    url = path.format(runner_id="live")
    observations: list[RefusalObservation] = []

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, method, path, status, model)

    def request(headers: dict[str, str], *, target: str = url, version: str = "0.2") -> Response:
        return client.request(method, target, headers=headers, json={"workspace_id": "w1", "version": version})

    other = hub.add_tenant("other")
    for headers, status, model in (
        ({}, 401, AuthenticationRequiredProblem),
        (bearer("invalid"), 401, AuthenticationUnresolvedProblem),
        (bearer(revoked), 401, AuthenticationRevokedProblem),
        (bearer(retired), 403, RunnerRetiredProblem),
        ({**bearer(live), "X-Blizzard-Tenant": other.tenant}, 400, TenantCredentialMismatchProblem),
    ):
        observe(request(headers), status, model)
    if kind != "registration":
        observe(request(bearer(live), target=path.format(runner_id="other")), 403, RunnerOwnershipProblem)
    if kind == "read":
        observe(request(bearer(live)), 409, RunnerNotRegisteredProblem)
    if kind == "registration":
        observe(request(bearer(live), version="0.1"), 409, RunnerVersionProblem)
    if kind in ("read", "registration"):
        with monkeypatch.context() as patch:

            def vanish_after_auth(
                request: Request,
                principal: RunnerPrincipal = Depends(require_runner_principal),
                tenant: TenantRequest = Depends(resolve_tenant),
            ) -> FleetRequest:
                fleet = FleetRequest.of(request, principal, tenant)

                def missing_runner(_runner_id: str) -> None:
                    return None

                patch.setattr(hub.services.registry, "get_runner", missing_runner)
                return fleet

            hub.app.dependency_overrides[FleetRequest.of] = vanish_after_auth
            observe(request(bearer(live)), 404, RunnerNotFoundProblem)
            del hub.app.dependency_overrides[FleetRequest.of]

    services = hub.app.state.services
    hub.app.state.services = None
    observe(request(bearer(live)), 503, HubStoreUnavailableProblem)
    hub.app.state.services = services

    def close_before_resolution(request: Request, services: Hub) -> TenantRequest:
        principal = RunnerAuth.of(request, services).demand()
        close_tenant(hub.engine, principal.tenant_id)
        services.tenants.open_tenants.refresh()
        return resolve_tenant(request, services)

    hub.app.dependency_overrides[resolve_tenant] = close_before_resolution
    observe(request(bearer(live)), 404, TenantNotFoundProblem)
    assert coverage_mismatches(hub.app.openapi(), {(method, path)}, observations) == []
