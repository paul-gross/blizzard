"""Observe every declared runner transcript problem through HTTP."""

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
from blizzard.wire.runner import RunnerRetiredProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from blizzard.wire.transcript_segment import LeaseOwnershipProblem
from tests.support.credential_runners import seed_credential_runners
from tests.support.hub_request_auth import bearer, ingest_chunk
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant, seed_runner


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/fleet/transcripts"),
        ("get", "/api/fleet/chunks/{chunk_id}/transcript-segments"),
    ],
)
def test_runner_transcript_problem_variants(tmp_path: Path, method: str, path: str) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    live, revoked, retired = seed_credential_runners(hub)
    chunk = ingest_chunk(hub)
    other = hub.add_tenant("other")
    url = path.format(chunk_id=chunk)
    observations: list[RefusalObservation] = []

    def request(headers: dict[str, str]) -> Response:
        if method == "post":
            return client.post(url, headers=headers, json={"records": []})
        return client.get(url, headers=headers, params={"node_id": "nd_build", "epoch": 1})

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, method, path, status, model)

    for headers, status, model in (
        ({}, 401, AuthenticationRequiredProblem),
        (bearer("unknown"), 401, AuthenticationUnresolvedProblem),
        (bearer(revoked), 401, AuthenticationRevokedProblem),
        ({**bearer(live), "X-Blizzard-Tenant": other.tenant}, 400, TenantCredentialMismatchProblem),
        (bearer(retired), 403, RunnerRetiredProblem),
    ):
        observe(request(headers), status, model)

    if method == "get":
        record: dict[str, object] = {
            "seq": 1,
            "segment_id": "sg_1",
            "chunk_id": chunk,
            "node_id": "nd_build",
            "epoch": 1,
            "spawn_generation": 1,
            "turn_range_start": 0,
            "turn_range_end": 0,
            "final": True,
            "normalizer_version": "v1",
            "harness_version": None,
            "turns": [],
        }
        response = client.post("/api/fleet/transcripts", headers=bearer(live), json={"records": [record]})
        assert response.status_code == 200, response.text
        foreign = seed_runner(hub, "foreign")
        observe(request(bearer(foreign)), 403, LeaseOwnershipProblem)

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
