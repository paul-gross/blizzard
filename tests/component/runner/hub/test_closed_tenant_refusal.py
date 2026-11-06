"""The host client understands the hub's closed-tenant race refusal."""

from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.hub.api.auth_session import refuse_closed_tenant
from blizzard.hub.tenancy import TenantClosed, TenantId
from blizzard.runner.hub.client import HostRefusalReason, HostRefused, HubClientError
from blizzard.runner.hub.internal.http_hub import HttpHostHubClient
from blizzard.wire.problem import TENANT_NOT_FOUND
from blizzard.wire.tenant import TenantNotFoundProblem
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import build_hub


def test_closed_tenant_race_is_a_host_refusal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    response_schema = hub.app.openapi()["paths"]["/api/fleet/host"]["get"]["responses"]["404"]
    assert "application/problem+json" in response_schema["content"]

    app = FastAPI()
    app.add_exception_handler(TenantClosed, refuse_closed_tenant)

    @app.get("/api/fleet/host")
    def closed() -> None:
        raise TenantClosed(TenantId("tenant"))

    with TestClient(app) as transport:
        response = transport.get("/api/fleet/host")
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["type"] == TENANT_NOT_FOUND
        assert (
            refusal_mismatches(
                hub.app.openapi(),
                RefusalObservation(
                    "get",
                    "/api/fleet/host",
                    404,
                    response.headers["content-type"],
                    TenantNotFoundProblem,
                    response.json(),
                ),
            )
            == []
        )
        refusal = HttpHostHubClient(transport, token="host-token").record()
    assert isinstance(refusal, HostRefused)
    assert refusal.reason == HostRefusalReason.NOT_FOUND
    assert refusal.detail == "no such tenant"


def test_legacy_untyped_404_remains_a_host_refusal() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"detail": "no such tenant"})

    with httpx2.Client(transport=httpx2.MockTransport(respond), base_url="http://hub") as transport:
        refusal = HttpHostHubClient(transport, token="host-token").record()
    assert isinstance(refusal, HostRefused)
    assert refusal.reason == HostRefusalReason.NOT_FOUND


def test_host_client_does_not_treat_an_unrelated_typed_404_as_missing() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"type": "urn:other", "detail": "other"})

    with (
        httpx2.Client(transport=httpx2.MockTransport(respond), base_url="http://hub") as transport,
        pytest.raises(HubClientError),
    ):
        HttpHostHubClient(transport, token="host-token").record()
