"""Aggregate runtime refusals for route release and token rotation."""

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
from blizzard.wire.release import ReleaseProblem
from blizzard.wire.route import RouteNotFoundProblem, RouteNotHeldProblem, RouteTerminalProblem
from blizzard.wire.runner import RunnerRetiredProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.credential_runners import seed_credential_runners
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant, ingest, report_lease, seed_runner


@pytest.mark.parametrize("suffix", ["release", "route-token"])
def test_route_mutation_problem_variants_are_aggregated(tmp_path: Path, suffix: str) -> None:
    hub = build_hub(tmp_path)
    holder = seed_runner(hub, "holder")
    other = seed_runner(hub, "other")
    _, revoked, retired = seed_credential_runners(hub)
    if suffix == "route-token":
        graph = """name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Complete.
          to: done
"""
        assert hub.client.post("/api/graphs", json={"definition_yaml": graph}).status_code == 201
    chunk = ingest(hub, [{"source": "default", "ref": "mutation"}])
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk, "runner_id": "holder", "workspace_id": "w1", "environment_ids": []},
    )
    assert claim.status_code == 201, claim.text
    path = f"/api/fleet/chunks/{{chunk_id}}/{suffix}"
    url = path.replace("{chunk_id}", chunk)
    body = (
        {"runner_id": "holder", "epoch": 0, "reason": "throttled", "route_token": claim.json()["route_token"]}
        if suffix == "release"
        else {}
    )
    observations: list[RefusalObservation] = []

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, "post", path, status, model)

    assert hub.app is not None
    client = TestClient(hub.app)
    observe(client.post(url, json=body), 401, AuthenticationRequiredProblem)
    observe(client.post(url, json=body, headers=bearer("invalid")), 401, AuthenticationUnresolvedProblem)
    observe(client.post(url, json=body, headers=bearer(revoked)), 401, AuthenticationRevokedProblem)
    observe(client.post(url, json=body, headers=bearer(retired)), 403, RunnerRetiredProblem)
    observe(client.post(url, json=body, headers=bearer(other)), 403, RouteNotHeldProblem)
    missing = path.replace("{chunk_id}", "ch_missing")
    observe(
        client.post(missing, json=body, headers=bearer(holder)),
        404,
        ChunkNotFoundProblem if suffix == "release" else RouteNotFoundProblem,
    )
    if suffix == "release":
        observe(client.post(url, json=body, headers=bearer(holder)), 409, ReleaseProblem)
    else:
        done_chunk = ingest(hub, [{"source": "default", "ref": "done"}])
        done_claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": done_chunk, "runner_id": "holder", "workspace_id": "w1", "environment_ids": []},
        )
        assert done_claim.status_code == 201, done_claim.text
        node = hub.client.get(f"/api/chunks/{done_chunk}").json()["current_node_id"]
        report_lease(hub, done_chunk, epoch=1, seq=1, runner_id="holder")
        done = hub.client.post(
            f"/api/fleet/chunks/{done_chunk}/completions",
            json={"choice": "pass", "epoch": 1, "runner_id": "holder", "from_node_id": node, "artifacts": []},
        )
        assert done.status_code == 200 and done.json()["outcome"] == "done", done.text
        observe(client.post(path.replace("{chunk_id}", done_chunk), headers=bearer(holder)), 409, RouteTerminalProblem)
    tenant = hub.add_tenant("other-tenant")
    observe(
        client.post(url, json=body, headers={**bearer(holder), "X-Blizzard-Tenant": tenant.tenant}),
        400,
        TenantCredentialMismatchProblem,
    )
    services = hub.app.state.services
    hub.app.state.services = None
    observe(client.post(url, json=body, headers=bearer(holder)), 503, HubStoreUnavailableProblem)
    hub.app.state.services = services

    def close_before_resolution(request: Request, services: Hub) -> TenantRequest:
        principal = RunnerAuth.of(request, services).demand()
        close_tenant(hub.engine, principal.tenant_id)
        services.tenants.open_tenants.refresh()
        return resolve_tenant(request, services)

    hub.app.dependency_overrides[resolve_tenant] = close_before_resolution
    observe(client.post(url, json=body, headers=bearer(holder)), 404, TenantNotFoundProblem)
    assert coverage_mismatches(hub.app.openapi(), {("post", path)}, observations) == []
