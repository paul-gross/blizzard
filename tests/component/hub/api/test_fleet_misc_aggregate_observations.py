"""Observe all declared problem variants on fleet read endpoints."""

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
from blizzard.wire.chunk import ChunkNotFoundProblem
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.question import QuestionNotFoundProblem
from blizzard.wire.runner import RunnerRetiredProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.credential_runners import seed_credential_runners
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant


@pytest.mark.parametrize(
    "path",
    [
        "/api/fleet/chunk-statuses",
        "/api/fleet/questions/{question_id}",
        "/api/fleet/scopes",
        "/api/fleet/summary",
    ],
)
def test_fleet_read_problem_variants(tmp_path: Path, path: str) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    live, revoked, retired = seed_credential_runners(hub)
    other = hub.add_tenant("other")
    url = path.format(question_id="missing")
    params = {"chunk_id": "ch_missing"} if path.endswith(("chunk-statuses", "scopes")) else {}
    observations: list[RefusalObservation] = []

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, "get", path, status, model)

    for headers, status, model in (
        ({}, 401, AuthenticationRequiredProblem),
        (bearer("unknown"), 401, AuthenticationUnresolvedProblem),
        (bearer(revoked), 401, AuthenticationRevokedProblem),
        (bearer(retired), 403, RunnerRetiredProblem),
        ({**bearer(live), "X-Blizzard-Tenant": other.tenant}, 400, TenantCredentialMismatchProblem),
    ):
        observe(client.get(url, headers=headers, params=params), status, model)
    if path.endswith("scopes"):
        observe(client.get(url, headers=bearer(live), params=params), 404, ChunkNotFoundProblem)
    if "{question_id}" in path:
        observe(client.get(url, headers=bearer(live)), 404, QuestionNotFoundProblem)

    services = hub.app.state.services
    hub.app.state.services = None
    observe(client.get(url, headers=bearer(live), params=params), 503, HubStoreUnavailableProblem)
    hub.app.state.services = services

    def close_before_resolution(request: Request, services: Hub) -> TenantRequest:
        principal = RunnerAuth.of(request, services).demand()
        close_tenant(hub.engine, principal.tenant_id)
        services.tenants.open_tenants.refresh()
        return resolve_tenant(request, services)

    hub.app.dependency_overrides[resolve_tenant] = close_before_resolution
    observe(client.get(url, headers=bearer(live), params=params), 404, TenantNotFoundProblem)
    assert coverage_mismatches(hub.app.openapi(), {("get", path)}, observations) == []
