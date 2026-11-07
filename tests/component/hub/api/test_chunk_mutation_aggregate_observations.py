"""Aggregate problem responses on runner chunk execution mutations."""

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
from blizzard.wire.route import PinnedGraphMissingProblem
from blizzard.wire.runner import RunnerRetiredProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.credential_runners import seed_credential_runners
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant


@pytest.mark.parametrize("suffix", ["hub-advance", "completions", "decisions"])
def test_chunk_mutation_problem_variants_are_aggregated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    live, revoked, retired = seed_credential_runners(hub)
    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    other = hub.add_tenant("other")
    path = f"/api/fleet/chunks/{{chunk_id}}/{suffix}"
    url = path.format(chunk_id="ch_missing")
    body = {"from_node_id": "node", "epoch": 1, "choice": "pass"}
    observations: list[RefusalObservation] = []

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, "post", path, status, model)

    for headers, status, model in (
        ({}, 401, AuthenticationRequiredProblem),
        (bearer("invalid"), 401, AuthenticationUnresolvedProblem),
        (bearer(revoked), 401, AuthenticationRevokedProblem),
        (bearer(retired), 403, RunnerRetiredProblem),
        ({**bearer(live), "X-Blizzard-Tenant": other.tenant}, 400, TenantCredentialMismatchProblem),
        (bearer(live), 404, ChunkNotFoundProblem),
    ):
        observe(client.post(url, json=body, headers=headers), status, model)

    def missing_graph(_graph_id: str) -> None:
        return None

    with monkeypatch.context() as patch:
        patch.setattr(hub.services.graphs, "get", missing_graph)
        observe(
            client.post(path.format(chunk_id=chunk), json=body, headers=bearer(live)), 500, PinnedGraphMissingProblem
        )

    services = hub.app.state.services
    hub.app.state.services = None
    observe(client.post(url, json=body, headers=bearer(live)), 503, HubStoreUnavailableProblem)
    hub.app.state.services = services

    def close_before_resolution(request: Request, services: Hub) -> TenantRequest:
        principal = RunnerAuth.of(request, services).demand()
        close_tenant(hub.engine, principal.tenant_id)
        services.tenants.open_tenants.refresh()
        return resolve_tenant(request, services)

    hub.app.dependency_overrides[resolve_tenant] = close_before_resolution
    observe(client.post(url, json=body, headers=bearer(live)), 404, TenantNotFoundProblem)
    assert coverage_mismatches(hub.app.openapi(), {("post", path)}, observations) == []
