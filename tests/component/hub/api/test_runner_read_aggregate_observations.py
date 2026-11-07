"""Aggregate real problem responses for runner chunk reads."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.api.auth import RunnerAuth
from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    AuthenticationRevokedProblem,
    AuthenticationUnresolvedProblem,
)
from blizzard.wire.chunk import ChunkNotFoundProblem, NoCurrentNodeProblem, NoRunContextProblem
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.route import MalformedCursorProblem, PinnedGraphMissingProblem
from blizzard.wire.runner import RunnerRetiredProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.credential_runners import seed_credential_runners
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import (
    build_hub,
    close_tenant,
    default_project,
    default_tenant,
    hub_store_connections,
    seed_scope,
    seed_work_item,
)


@pytest.mark.parametrize(
    "suffix", ["", "/envelope", "/work-items", "/pm-items", "/garden/proposals", "/garden/proposals/page"]
)
def test_chunk_read_problem_variants_are_aggregated(
    tmp_path: Path, suffix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    path = f"/api/fleet/chunks/{{chunk_id}}{suffix}"
    url = path.format(chunk_id="ch_missing")
    live, revoked, retired = seed_credential_runners(hub)
    created = hub.client.post("/api/chunks", json={"tokens": ["default:1"]})
    assert created.status_code == 201
    other = hub.add_tenant("other")
    observations: list[RefusalObservation] = []

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, "get", path, status, model)

    for headers, status, model in (
        ({}, 401, AuthenticationRequiredProblem),
        (bearer("unknown"), 401, AuthenticationUnresolvedProblem),
        (bearer(revoked), 401, AuthenticationRevokedProblem),
        (bearer(retired), 403, RunnerRetiredProblem),
        ({**bearer(live), "X-Blizzard-Tenant": other.tenant}, 400, TenantCredentialMismatchProblem),
        (bearer(live), 404, ChunkNotFoundProblem),
    ):
        observe(client.get(url, headers=headers), status, model)
    if suffix.startswith("/garden/"):
        observe(
            client.get(path.format(chunk_id=created.json()["chunk_id"]), headers=bearer(live)), 404, NoRunContextProblem
        )
    if suffix == "/garden/proposals/page":
        connections = hub_store_connections(hub.engine)
        now = datetime(2026, 9, 2, tzinfo=UTC)
        item = seed_work_item(connections, graph_id="gr_garden", author=WorkItemAuthor.user("u_1"), at=now)
        seed_scope(connections, "blizzard", at=now)
        RunContextStore(connections).record(
            item.work_item_id,
            RunContext(
                project_id=default_project(hub.engine), routine_name="nightly", scope_slug="blizzard", mode="dry_run"
            ),
        )
        malformed = client.get(
            path.format(chunk_id=f"ch_{item.ref}"), params={"cursor": "invalid!"}, headers=bearer(live)
        )
        assert malformed.json()["detail"] == "malformed cursor"
        observe(malformed, 422, MalformedCursorProblem)
    if suffix == "/envelope":
        chunk = created.json()["chunk_id"]
        stopped_response = hub.client.post(
            f"/api/chunks/{chunk}/stop",
            json={"by": "operator"},
            headers={"X-Blizzard-Tenant": default_tenant(hub.engine)},
        )
        assert stopped_response.status_code == 202, stopped_response.text
        stopped = client.get(path.format(chunk_id=chunk), headers=bearer(live))
        observe(stopped, 409, NoCurrentNodeProblem)

        def no_graph(_graph_id: str) -> None:
            return None

        with monkeypatch.context() as patch:
            patch.setattr(hub.services.graphs, "get", no_graph)
            missing_graph = client.get(path.format(chunk_id=chunk), headers=bearer(live))
        assert missing_graph.json()["detail"] == "chunk's pinned graph is missing"
        observe(missing_graph, 500, PinnedGraphMissingProblem)

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
