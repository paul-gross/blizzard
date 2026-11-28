"""Aggregate actual artifact-check refusals, including shared dependencies."""

from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.api.auth import RunnerAuth
from blizzard.hub.api.auth_session import AdmittedTenant, resolve_tenant
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.open_tenants import OpenTenants
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
from tests.support.direct_auth import admission_for, machine_for
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, close_tenant, ingest
from tests.support.store_free_process import store_free


def test_artifact_check_problem_variants_are_aggregated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    path = "/api/fleet/chunks/{chunk_id}/artifact-check"
    chunk = ingest(hub, [{"source": "default", "ref": "artifact-refusals"}])
    url = path.format(chunk_id=chunk)
    body = {"name": "delta", "content": "x"}
    live, revoked, retired = seed_credential_runners(hub)
    other = hub.add_tenant("other")
    before = hub.default_adapter(IReadChunkRecordRepository).get(chunk)
    facts = hub.default_adapter(IReadChunkFactsRepository).load_facts(chunk)
    observations: list[RefusalObservation] = []

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, "post", path, status, model)

    for headers, status, model in (
        ({}, 401, AuthenticationRequiredProblem),
        (bearer("unknown"), 401, AuthenticationUnresolvedProblem),
        (bearer(revoked), 401, AuthenticationRevokedProblem),
        (bearer(retired), 403, RunnerRetiredProblem),
        ({**bearer(live), "X-Blizzard-Tenant": other.tenant}, 400, TenantCredentialMismatchProblem),
    ):
        observe(client.post(url, json=body, headers=headers), status, model)
    observe(client.post(path.format(chunk_id="ch_missing"), json=body, headers=bearer(live)), 404, ChunkNotFoundProblem)

    def missing_graph(graph_id: str) -> None:
        return None

    with monkeypatch.context() as patch:
        patch.setattr(hub.default_adapter(IReadGraphRepository), "get", missing_graph)
        observe(client.post(url, json=body, headers=bearer(live)), 500, PinnedGraphMissingProblem)
    with store_free(hub.app):
        observe(client.post(url, json=body, headers=bearer(live)), 503, HubStoreUnavailableProblem)
    assert hub.default_adapter(IReadChunkRecordRepository).get(chunk) == before
    assert hub.default_adapter(IReadChunkFactsRepository).load_facts(chunk) == facts

    def close_before_resolution(request: Request) -> AdmittedTenant:
        principal = RunnerAuth.of(request, machine_for(hub.process)).demand()
        close_tenant(hub.engine, principal.tenant_id)
        hub.resolve_hub(OpenTenants).refresh()
        return admission_for(request, hub.process)

    hub.app.dependency_overrides[resolve_tenant] = close_before_resolution
    observe(client.post(url, json=body, headers=bearer(live)), 404, TenantNotFoundProblem)
    assert coverage_mismatches(hub.app.openapi(), {("post", path)}, observations) == []
