"""Observe shared host authentication and tenant refusals per operation."""

from pathlib import Path
from typing import cast

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.foundation.tokens import TokenHash
from blizzard.hub.api.auth import HostAuth
from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.runners.host import HostTokenRotation
from blizzard.hub.domain.runners.lifecycle_version import RunnerLifecycleVersion
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    AuthenticationRevokedProblem,
    AuthenticationUnresolvedProblem,
)
from blizzard.wire.host import HostRetiredProblem
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.runner import (
    RunnerHoldsRoutesProblem,
    RunnerLifecycleConflictProblem,
    RunnerNotFoundProblem,
    RunnerRetireInterruptedProblem,
    UnknownServedProjectProblem,
)
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import (
    RefusalObservation,
    coverage_mismatches,
    observe_refusal,
    refusal_mismatches,
)
from tests.support.scaffolding import (
    build_hub,
    close_tenant,
    default_tenant,
    hub_store_connections,
    ingest,
    seed_runner,
)

OPERATIONS = [
    ("GET", "/api/fleet/host"),
    ("POST", "/api/fleet/host"),
    ("POST", "/api/fleet/host/events"),
    ("POST", "/api/fleet/host/runners"),
    ("POST", "/api/fleet/host/runners/{runner_id}/retire"),
]


@pytest.mark.parametrize(("method", "path"), OPERATIONS)
def test_shared_refusals_match_each_host_operation(
    tmp_path: Path, method: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    revoked_host = hub.client.post("/api/hosts", json={"name": "revoked"})
    live_host = hub.client.post("/api/hosts", json={"name": "live"})
    assert revoked_host.status_code == live_host.status_code == 201
    revoked = hub.client.post(f"/api/hosts/{revoked_host.json()['host_id']}/token-revocations", json={})
    assert revoked.status_code == 201
    runner_token = seed_runner(hub, "runner-a")
    other = hub.add_tenant("other")
    hub.client.headers["X-Blizzard-Tenant"] = default_tenant(hub.engine)
    url = path.format(runner_id="rn_missing")
    cases = [
        (dict[str, str](), 401, AuthenticationRequiredProblem, "missing or malformed Authorization header"),
        (
            bearer("not-a-real-token"),
            401,
            AuthenticationUnresolvedProblem,
            "bearer token does not resolve to a known host",
        ),
        (bearer(runner_token), 401, AuthenticationUnresolvedProblem, "bearer token does not resolve to a known host"),
        (bearer(revoked_host.json()["token"]), 401, AuthenticationRevokedProblem, "bearer token has been revoked"),
        (
            {**bearer(live_host.json()["token"]), "X-Blizzard-Tenant": other.tenant},
            400,
            TenantCredentialMismatchProblem,
            None,
        ),
    ]
    observations: list[RefusalObservation] = []

    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, method, path, status, model)

    for headers, status, model, detail in cases:
        response = client.request(method, url, headers=headers, json={})
        observation = observe_refusal(observations, response, method, path, status, model)
        if detail is not None:
            assert response.json()["detail"] == detail
        assert refusal_mismatches(hub.app.openapi(), observation) == []

    headers = bearer(live_host.json()["token"])
    if path == "/api/fleet/host/runners":
        observe(
            client.post(url, json={"name": "runner", "projects": ["missing"]}, headers=headers),
            422,
            UnknownServedProjectProblem,
        )
    if path.endswith("/{runner_id}/retire"):
        observe(client.post(url, json={}, headers=headers), 404, RunnerNotFoundProblem)
        assert (
            hub.client.post(
                f"/api/hosts/{live_host.json()['host_id']}/adoptions", json={"runner_ids": ["runner-a"]}
            ).status_code
            == 200
        )
        chunk = ingest(hub, [{"source": "default", "ref": "host-retire"}])
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk, "runner_id": "runner-a", "workspace_id": "w1", "environment_ids": []},
        )
        assert claim.status_code == 201, claim.text
        retire_url = path.format(runner_id="runner-a")
        observe(client.post(retire_url, json={}, headers=headers), 409, RunnerHoldsRoutesProblem)
        registry = cast(IWriteRunnerRegistry, hub.services.registry)
        holdings = hub.services.fleet.holdings

        def retire_after_read(runner_id: str) -> list[Route]:
            routes = holdings(runner_id)
            registry.record_lifecycle(runner_id, retired=True, at=hub.clock.now(), by="racer", expected_lifecycle=0)
            return routes

        with monkeypatch.context() as patch:
            patch.setattr(hub.services.fleet, "holdings", retire_after_read)
            observe(client.post(retire_url, json={"force": True}, headers=headers), 409, RunnerLifecycleConflictProblem)

        registration = registry.get_runner("runner-a")
        assert registration is not None
        registry.record_lifecycle(
            "runner-a", retired=False, at=hub.clock.now(), by="racer", expected_lifecycle=registration.lifecycle_version
        )
        release = hub.services.detach.release_held

        def reinstate_before_release(chunk: Chunk, *, runner: RunnerLifecycleVersion) -> int | None:
            registration = registry.get_runner("runner-a")
            assert registration is not None and registration.retired
            registry.record_lifecycle(
                "runner-a",
                retired=False,
                at=hub.clock.now(),
                by="racer",
                expected_lifecycle=registration.lifecycle_version,
            )
            return release(chunk, runner=runner)

        with monkeypatch.context() as patch:
            patch.setattr(hub.services.detach, "release_held", reinstate_before_release)
            observe(client.post(retire_url, json={"force": True}, headers=headers), 409, RunnerRetireInterruptedProblem)

    retired_host = hub.client.post("/api/hosts", json={"name": "retired"})
    assert retired_host.status_code == 201, retired_host.text
    host_id = retired_host.json()["host_id"]
    assert hub.client.post(f"/api/hosts/{host_id}/retire", json={}).status_code == 200
    store = HostRegistryStore(hub_store_connections(hub.engine))
    store.rotate_token(HostTokenRotation(host_id, TokenHash("late").hex, hub.clock.now(), "racer"))
    observe(client.request(method, url, headers=bearer("late"), json={}), 403, HostRetiredProblem)

    services = hub.app.state.services
    hub.app.state.services = None
    try:
        response = client.request(method, url, headers=bearer(live_host.json()["token"]), json={})
    finally:
        hub.app.state.services = services
    assert response.status_code == 503, response.text
    observations.append(
        RefusalObservation(
            method,
            path,
            response.status_code,
            response.headers["content-type"],
            HubStoreUnavailableProblem,
            response.json(),
        )
    )

    def close_before_resolution(request: Request, services: Hub) -> TenantRequest:
        principal = HostAuth.of(request, services).demand()
        close_tenant(hub.engine, principal.tenant_id)
        services.tenants.open_tenants.refresh()
        return resolve_tenant(request, services)

    hub.app.dependency_overrides[resolve_tenant] = close_before_resolution
    response = client.request(method, url, headers=bearer(live_host.json()["token"]), json={})
    assert response.status_code == 404, response.text
    observations.append(
        RefusalObservation(
            method, path, response.status_code, response.headers["content-type"], TenantNotFoundProblem, response.json()
        )
    )
    assert coverage_mismatches(hub.app.openapi(), {(method, path)}, observations) == []
