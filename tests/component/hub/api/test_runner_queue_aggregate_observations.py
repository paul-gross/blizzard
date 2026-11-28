"""Aggregate actual problem responses for both runner queue peeks."""

from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.api.auth import RunnerAuth
from blizzard.hub.api.auth_session import AdmittedTenant, resolve_tenant
from blizzard.hub.open_tenants import OpenTenants
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
from tests.support.direct_auth import admission_for, machine_for
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant
from tests.support.store_free_process import store_free


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

    with store_free(hub.app):
        observe(request(bearer(live)), 503, HubStoreUnavailableProblem)

    def close_before_resolution(request: Request) -> AdmittedTenant:
        principal = RunnerAuth.of(request, machine_for(hub.process)).demand()
        close_tenant(hub.engine, principal.tenant_id)
        hub.resolve_hub(OpenTenants).refresh()
        return admission_for(request, hub.process)

    hub.app.dependency_overrides[resolve_tenant] = close_before_resolution
    observe(request(bearer(live)), 404, TenantNotFoundProblem)
    assert coverage_mismatches(hub.app.openapi(), {(method, path)}, observations) == []
