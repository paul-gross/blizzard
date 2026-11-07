"""Aggregate observed claim refusals against the operation's declaration."""

from pathlib import Path
from typing import cast

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.api.auth import RunnerAuth
from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.domain.chunk.dependencies import PrerequisiteScope
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry
from blizzard.hub.domain.runners.version_window import VersionWindow
from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    AuthenticationRevokedProblem,
    AuthenticationUnresolvedProblem,
)
from blizzard.wire.chunk import ChunkNotFoundProblem
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.route import (
    ClaimRunnerRetiredProblem,
    ClaimRunnerUnregisteredProblem,
    ClaimRunnerVersionRefusedProblem,
    PinnedGraphMissingProblem,
    RouteClaimConflictProblem,
    RouteClaimDependencyProblem,
    RouteClaimIncompatibleProblem,
    RouteClaimNotReadyProblem,
    RouteClaimPausedProblem,
    RouteClaimTerminalProblem,
)
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant, ingest, seed_runner


def test_claim_problem_variants_are_aggregated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(
        tmp_path, auto_register_claimants=False, hub_version="0.3", version_window=VersionWindow(floor="0.2")
    )
    seed_runner(hub, "r1")
    seed_runner(hub, "r2")
    seed_runner(hub, "r3", projects=())
    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    path = "/api/fleet/routes"
    observations: list[RefusalObservation] = []

    def claim(runner: str, chunk_id: str = chunk) -> Response:
        return hub.client.post(
            path, json={"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": []}
        )

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, "post", path, status, model)

    observe(claim("r1"), 409, RouteClaimNotReadyProblem)
    assert hub.client.post(f"/api/chunks/{chunk}/promote").status_code == 202
    assert claim("r1").status_code == 201
    observe(claim("r2"), 409, RouteClaimConflictProblem)
    incompatible = ingest(hub, [{"source": "default", "ref": "incompatible"}])
    observe(claim("r3", incompatible), 409, RouteClaimIncompatibleProblem)
    registration = hub.client.post(
        "/api/fleet/runners",
        json={
            "runner_id": "wrong-harness",
            "workspace_id": "w1",
            "capabilities": [{"harness_id": "other", "default": True}],
        },
    )
    assert registration.status_code == 201, registration.text
    capability_chunk = hub.client.post("/api/chunks", json={"tokens": ["default:capability"]}).json()["chunk_id"]
    assert (
        hub.client.patch(f"/api/chunks/{capability_chunk}", json={"default_harnesses": ["special-harness"]}).status_code
        == 202
    )
    assert hub.client.post(f"/api/chunks/{capability_chunk}/promote").status_code == 202
    capability = claim("wrong-harness", capability_chunk)
    observe(capability, 409, RouteClaimIncompatibleProblem)
    assert capability.json()["reason"] == "capability"
    assert hub.client.get(f"/api/chunks/{capability_chunk}").json()["route"] is None
    terminal = ingest(hub, [{"source": "default", "ref": "terminal"}])
    assert hub.client.post(f"/api/chunks/{terminal}/stop", json={"by": "operator"}).status_code == 202
    observe(claim("r1", terminal), 409, RouteClaimTerminalProblem)
    dependent = ingest(hub, [{"source": "default", "ref": "dependent"}])
    prerequisite = ingest(hub, [{"source": "default", "ref": "prerequisite"}])
    record = hub.services.chunks.record.get(dependent)
    assert record is not None
    hub.services.dependencies.declare(record, PrerequisiteScope(prerequisite), by="user:alice")
    observe(claim("r1", dependent), 409, RouteClaimDependencyProblem)
    observe(claim("r1", "ch_missing"), 404, ChunkNotFoundProblem)
    observe(claim("missing", dependent), 403, ClaimRunnerUnregisteredProblem)
    assert hub.client.post("/api/runners/r1/pause", json={"by": "operator"}).status_code == 200
    observe(claim("r1", dependent), 403, RouteClaimPausedProblem)
    cast(IWriteRunnerRegistry, hub.services.registry).record_lifecycle(
        "r2", retired=True, at=hub.clock.now(), by="operator", expected_lifecycle=0
    )
    observe(claim("r2", dependent), 403, ClaimRunnerRetiredProblem)
    body = {"runner_id": "versioned", "workspace_id": "w1", "capabilities": [{"harness_id": "claude", "default": True}]}
    assert hub.client.post("/api/fleet/runners", json={**body, "version": "0.2.1"}).status_code == 201
    assert hub.client.post("/api/fleet/runners", json={**body, "version": "0.1.4"}).status_code == 409
    observe(claim("versioned", dependent), 403, ClaimRunnerVersionRefusedProblem)
    graph_chunk = ingest(hub, [{"source": "default", "ref": "graph"}])

    def missing_graph(_graph_id: str) -> None:
        return None

    with monkeypatch.context() as patch:
        patch.setattr(hub.services.graphs, "get", missing_graph)
        observe(claim("r3", graph_chunk), 500, PinnedGraphMissingProblem)
    assert hub.app is not None
    client = TestClient(hub.app)
    token = seed_runner(hub, "auth-runner")
    assert hub.client.post("/api/runners/auth-runner/token-revocations", json={"by": "operator"}).status_code == 201
    live = seed_runner(hub, "live-runner")
    other = hub.add_tenant("other")
    observe(client.post(path, json={}), 401, AuthenticationRequiredProblem)
    observe(client.post(path, json={}, headers=bearer("invalid")), 401, AuthenticationUnresolvedProblem)
    observe(client.post(path, json={}, headers=bearer(token)), 401, AuthenticationRevokedProblem)
    observe(
        client.post(path, json={}, headers={**bearer(live), "X-Blizzard-Tenant": other.tenant}),
        400,
        TenantCredentialMismatchProblem,
    )
    services = hub.app.state.services
    hub.app.state.services = None
    observe(client.post(path, json={}, headers=bearer(live)), 503, HubStoreUnavailableProblem)
    hub.app.state.services = services

    def close_before_resolution(request: Request, services: Hub) -> TenantRequest:
        principal = RunnerAuth.of(request, services).demand()
        close_tenant(hub.engine, principal.tenant_id)
        services.tenants.open_tenants.refresh()
        return resolve_tenant(request, services)

    hub.app.dependency_overrides[resolve_tenant] = close_before_resolution
    observe(claim("r3", graph_chunk), 404, TenantNotFoundProblem)
    assert coverage_mismatches(hub.app.openapi(), {("post", path)}, observations) == []
