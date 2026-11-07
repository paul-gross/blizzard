"""Tenant closure between request resolution and a scoped write."""

from pathlib import Path

import pytest
from fastapi import Request

from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant
from blizzard.hub.api.hub_level import Hub
from blizzard.wire.auth import AuthenticationUnresolvedProblem
from blizzard.wire.problem import AUTHENTICATION_UNRESOLVED, TENANT_NOT_FOUND
from blizzard.wire.tenant import TenantNotFoundProblem
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import build_hub, close_tenant, seed_runner


@pytest.mark.parametrize("operation", ["bootstrap", "host", "runner"])
def test_closure_after_resolution_refuses_the_write(tmp_path: Path, operation: str) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    host = hub.client.post("/api/hosts", json={"name": "box"})
    assert host.status_code == 201
    runner_token = seed_runner(hub, "r1")
    resolved: list[str] = []

    def close_after_resolution(request: Request, services: Hub) -> TenantRequest:
        tenant = resolve_tenant(request, services)
        resolved.append(tenant.scope.tenant)
        close_tenant(hub.engine, tenant.scope.tenant)
        return tenant

    hub.app.dependency_overrides[resolve_tenant] = close_after_resolution
    if operation == "bootstrap":
        path = template = "/api/hosts"
        response = hub.client.post(path + "?ignored=not-a-secret", json={"name": "another"})
    elif operation == "host":
        path = template = "/api/fleet/host/runners"
        response = hub.client.post(
            path + "?ignored=not-a-secret", json={"name": "another"}, headers=bearer(host.json()["token"])
        )
    else:
        path = template = "/api/fleet/runners"
        response = hub.client.post(
            path + "?ignored=not-a-secret", json={"workspace_id": "w1"}, headers=bearer(runner_token)
        )

    assert len(resolved) == 1
    model = AuthenticationUnresolvedProblem if operation == "runner" else TenantNotFoundProblem
    status = 401 if operation == "runner" else 404
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body == {
        "type": AUTHENTICATION_UNRESOLVED if operation == "runner" else TENANT_NOT_FOUND,
        "title": "Authentication unresolved" if operation == "runner" else "Tenant not found",
        "detail": "bearer token does not resolve to a known runner" if operation == "runner" else "no such tenant",
        "instance": path,
        "status": status,
    }
    observation = RefusalObservation("post", template, status, response.headers["content-type"], model, body)
    assert refusal_mismatches(hub.app.openapi(), observation) == []
