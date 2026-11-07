"""Aggregate the observed fleet pause and resume problem responses."""

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
from blizzard.wire.chunk import ChunkNotFoundProblem, ChunkNotPausableProblem
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.runner import RunnerRetiredProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.credential_runners import seed_credential_runners
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant


@pytest.mark.parametrize("operation", ["pause", "resume"])
def test_fleet_pause_and_resume_problem_variants(tmp_path: Path, operation: str) -> None:
    hub = build_hub(tmp_path)
    live, revoked, retired = seed_credential_runners(hub)
    created = hub.client.post("/api/chunks", json={"tokens": ["default:pause-observation"]})
    assert created.status_code == 201, created.text
    chunk = created.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk}/stop", json={"by": "operator"}).status_code == 202

    path = f"/api/fleet/chunks/{{chunk_id}}/{operation}"
    url = path.replace("{chunk_id}", chunk)
    observations: list[RefusalObservation] = []

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, "post", path, status, model)

    assert hub.app is not None
    client = TestClient(hub.app)
    observe(client.post(url), 401, AuthenticationRequiredProblem)
    observe(client.post(url, headers=bearer("invalid")), 401, AuthenticationUnresolvedProblem)
    observe(client.post(url, headers=bearer(revoked)), 401, AuthenticationRevokedProblem)
    observe(client.post(url, headers=bearer(retired)), 403, RunnerRetiredProblem)
    observe(client.post(path.replace("{chunk_id}", "ch_missing"), headers=bearer(live)), 404, ChunkNotFoundProblem)
    if operation == "pause":
        observe(client.post(url, headers=bearer(live)), 409, ChunkNotPausableProblem)

    other = hub.add_tenant("other")
    observe(
        client.post(url, headers={**bearer(live), "X-Blizzard-Tenant": other.tenant}),
        400,
        TenantCredentialMismatchProblem,
    )
    services = hub.app.state.services
    hub.app.state.services = None
    observe(client.post(url, headers=bearer(live)), 503, HubStoreUnavailableProblem)
    hub.app.state.services = services

    def close_before_resolution(request: Request, services: Hub) -> TenantRequest:
        principal = RunnerAuth.of(request, services).demand()
        close_tenant(hub.engine, principal.tenant_id)
        services.tenants.open_tenants.refresh()
        return resolve_tenant(request, services)

    hub.app.dependency_overrides[resolve_tenant] = close_before_resolution
    observe(client.post(url, headers=bearer(live)), 404, TenantNotFoundProblem)
    assert coverage_mismatches(hub.app.openapi(), {("post", path)}, observations) == []
