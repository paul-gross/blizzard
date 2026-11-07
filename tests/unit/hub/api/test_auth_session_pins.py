"""Pinning tests for hub decisions that were previously defended by comment alone
(``bzh:mutation-review-selection``).

Each test exists because a long comment argued for a decision no assertion covered;
the comment at each site now points back at the test that fails on revert."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from blizzard.hub.api.auth import RunnerPrincipal
from blizzard.hub.api.auth_session import refuse_closed_tenant, require
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.config import HubConfig
from blizzard.hub.tenancy import TenantId, UnknownTenant
from blizzard.wire.problem import AUTHENTICATION_UNRESOLVED, TENANT_NOT_FOUND


def test_require_reports_the_store_unwired_with_no_store_wired() -> None:
    """A ``require()``-gated route serves a tenant, and a store-free app has none to resolve: under
    the default ``auth.mode = "none"`` the gate answers 503, never the implicit operator unscoped."""
    app = FastAPI()
    app.state.config = HubConfig(root=Path("."), db_url="sqlite://")  # `auth.mode` defaults to "none"
    app.state.services = None  # store-free, exactly as the export/unit app is built

    @app.get("/_pin_probe")
    def _probe(identity: ResolvedIdentity = Depends(require(Permission.FLEET_VIEW))) -> dict[str, str]:
        return {"username": identity.username}

    with TestClient(app) as client:
        resp = client.get("/_pin_probe")

    assert resp.status_code == 503, resp.text


def test_closed_tenant_handler_only_changes_declared_operation() -> None:
    app = FastAPI()
    app.add_exception_handler(UnknownTenant, refuse_closed_tenant)

    @app.post("/api/hosts")
    @app.post("/unrelated")
    def _probe(request: Request) -> None:
        raise UnknownTenant(TenantId("secret-tenant"))

    with TestClient(app) as client:
        declared = client.post("/api/hosts?token=secret")
        unrelated = client.post("/unrelated")

    assert declared.status_code == 404
    assert declared.headers["content-type"] == "application/problem+json"
    assert declared.json() == {
        "type": TENANT_NOT_FOUND,
        "title": "Tenant not found",
        "detail": "no such tenant",
        "instance": "/api/hosts",
        "status": 404,
    }
    assert unrelated.status_code == 404
    assert unrelated.json() == {"detail": "no such tenant"}
    assert unrelated.headers["content-type"] == "application/json"


def test_closed_tenant_runner_keeps_unresolved_credential_detail() -> None:
    app = FastAPI()
    app.add_exception_handler(UnknownTenant, refuse_closed_tenant)

    @app.get("/api/fleet/summary")
    def _probe(request: Request) -> None:
        request.scope["blizzard.runner_principal"] = RunnerPrincipal("id", "name", None, TenantId("tenant"))
        raise UnknownTenant(TenantId("tenant"))

    with TestClient(app) as client:
        response = client.get("/api/fleet/summary")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == AUTHENTICATION_UNRESOLVED
    assert response.json()["detail"] == "bearer token does not resolve to a known runner"
