"""Aggregate actual problem responses for both runner queue peeks."""

from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.api.auth import RunnerAuth
from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant
from blizzard.hub.api.hub_level import Hub
from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    AuthenticationRevokedProblem,
    AuthenticationUnresolvedProblem,
)
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.project import UnknownProjectProblem
from blizzard.wire.runner import RunnerRetiredProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.credential_runners import seed_credential_runners
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant


@pytest.mark.parametrize("method", ["get", "post"])
def test_runner_queue_problem_variants_are_aggregated(tmp_path: Path, method: str) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    path = "/api/fleet/queue/peek"
    live, revoked, retired = seed_credential_runners(hub)
    other = hub.add_tenant("other")
    observations: list[RefusalObservation] = []

    def request(headers: dict[str, str], *, unknown_project: bool = False) -> Response:
        if method == "get":
            return client.get(path, headers=headers, params={"project": "missing"} if unknown_project else {})
        return client.post(path, headers=headers, json={})

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, method, path, status, model)

    for headers, status, model in (
        ({}, 401, AuthenticationRequiredProblem),
        (bearer("unknown"), 401, AuthenticationUnresolvedProblem),
        (bearer(revoked), 401, AuthenticationRevokedProblem),
        (bearer(retired), 403, RunnerRetiredProblem),
        ({**bearer(live), "X-Blizzard-Tenant": other.tenant}, 400, TenantCredentialMismatchProblem),
    ):
        observe(request(headers), status, model)
    if method == "get":
        observe(request(bearer(live), unknown_project=True), 422, UnknownProjectProblem)

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
