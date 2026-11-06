"""Observe all declared problem variants on runner finding reads."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.api.auth import RunnerAuth
from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    AuthenticationRevokedProblem,
    AuthenticationUnresolvedProblem,
)
from blizzard.wire.chunk import (
    ChunkNotFoundProblem,
    FindingNotInChunkProblem,
    NoAnsweredProposalProblem,
    NoRunContextProblem,
)
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.runner import RunnerRetiredProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.credential_runners import seed_credential_runners
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant, default_project, hub_store_connections


@pytest.mark.parametrize("suffix", ["garden/findings", "findings", "findings/{finding_id}"])
def test_runner_findings_problem_variants_are_aggregated(tmp_path: Path, suffix: str) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    path = f"/api/fleet/chunks/{{chunk_id}}/{suffix}"
    url = path.format(chunk_id="ch_missing", finding_id="fin_missing")
    live, revoked, retired = seed_credential_runners(hub)
    created = hub.client.post("/api/chunks", json={"tokens": ["default:1"]})
    assert created.status_code == 201, created.text
    plain = created.json()["chunk_id"]
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        "gprop_observation",
        project_id=default_project(hub.engine),
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix",
        title="title",
        body="body",
        findings=[],
        at=datetime(2026, 9, 4, tzinfo=UTC),
    )
    accepted = hub.client.post("/api/garden-proposals/gprop_observation/accept", json={})
    assert accepted.status_code == 200, accepted.text
    answered = accepted.json()["chunk_id"]
    other = hub.add_tenant("other")
    observations: list[RefusalObservation] = []

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, "get", path, status, model)

    for headers, status, model in (
        ({}, 401, AuthenticationRequiredProblem),
        (bearer("unknown"), 401, AuthenticationUnresolvedProblem),
        (bearer(revoked), 401, AuthenticationRevokedProblem),
        ({**bearer(live), "X-Blizzard-Tenant": other.tenant}, 400, TenantCredentialMismatchProblem),
        (bearer(live), 404, ChunkNotFoundProblem),
    ):
        observe(client.get(url, headers=headers), status, model)
    observe(client.get(url, headers=bearer(retired)), 403, RunnerRetiredProblem)
    model = NoRunContextProblem if suffix == "garden/findings" else NoAnsweredProposalProblem
    observe(client.get(path.format(chunk_id=plain, finding_id="fin_missing"), headers=bearer(live)), 404, model)
    if suffix == "findings/{finding_id}":
        observe(
            client.get(path.format(chunk_id=answered, finding_id="fin_missing"), headers=bearer(live)),
            404,
            FindingNotInChunkProblem,
        )

    services = hub.app.state.services
    hub.app.state.services = None
    observe(client.get(url, headers=bearer(live)), 503, HubStoreUnavailableProblem)
    hub.app.state.services = services

    def close_before_resolution(request: Request, services: Hub) -> TenantRequest:
        principal = RunnerAuth.of(request, services).demand()
        close_tenant(hub.engine, principal.tenant_id)
        services.tenants.open_tenants.refresh()
        return resolve_tenant(request, services)

    hub.app.dependency_overrides[resolve_tenant] = close_before_resolution
    observe(client.get(url, headers=bearer(live)), 404, TenantNotFoundProblem)
    assert coverage_mismatches(hub.app.openapi(), {("get", path)}, observations) == []
