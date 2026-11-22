"""A tenant route resolves through the hub's container: its admission opens the tenant's scope off the event loop, its
request scope never closes the tenant, a failed open answers the unknown tenant's ``404``, and a deletion evicts the
tenant's scope and its legacy entry together (component tier)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient

from blizzard.foundation.registry.scopes import OwnedChild
from blizzard.hub.api.auth_session import (
    TENANT_HEADER,
    AdmittedTenant,
    implicit_operator,
    tenant_bound,
    tenant_request,
)
from blizzard.hub.api.resolution import request_resolver
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.scopes.tenants import TenantContainers
from blizzard.hub.tenancy import StoreScope, TenantClosed, TenantId, UnknownTenant
from blizzard.wire.tenant import TENANT_NOT_FOUND
from tests.support.scaffolding import HubHarness, build_hub, default_tenant, seed_runner


def _scratch_router() -> APIRouter:
    router = APIRouter(prefix="/scratch", dependencies=[Depends(tenant_bound)])

    @router.get("/bound")
    async def bound(request: Request) -> dict[str, object]:
        return {
            "log_tenant": structlog.contextvars.get_contextvars().get("tenant_id"),
            "state_tenant": request.state.tenant_id,
            "request_scope": request_resolver(request) is not request.app.state.hub_process.resolver,
        }

    @router.get("/fails")
    def fails() -> None:
        raise RuntimeError("the handler fails after its tenant was admitted")

    return router


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    harness = build_hub(tmp_path)
    assert harness.app is not None
    harness.app.include_router(_scratch_router())
    harness.app.router.routes.sort(key=lambda route: getattr(route, "name", None) == "web")
    return harness


def _client(hub: HubHarness) -> TestClient:
    assert hub.app is not None
    return TestClient(hub.app, raise_server_exceptions=False)


def _created(hub: HubHarness, name: str = "acme") -> TenantId:
    return hub.hub_services.tenant_admin.create(name, admin_user_id=None, by="usr_creator").tenant_id


def _containers(hub: HubHarness) -> TenantContainers:
    return hub.hub_services.process.tenants


def _spy_open(monkeypatch: pytest.MonkeyPatch, record: Callable[[StoreScope], None]) -> None:
    original = TenantContainers.open

    def spied(self: TenantContainers, scope: StoreScope) -> OwnedChild:
        record(scope)
        return original(self, scope)

    monkeypatch.setattr(TenantContainers, "open", spied)


def test_a_cache_miss_opens_the_tenants_scope_off_the_event_loop(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _created(hub)
    on_loop: list[bool] = []

    def record(scope: StoreScope) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            on_loop.append(False)
        else:
            on_loop.append(True)

    _spy_open(monkeypatch, record)

    response = _client(hub).get("/scratch/bound", headers={TENANT_HEADER: tenant})

    assert response.status_code == 200
    assert on_loop == [False]


def _refusing_open(monkeypatch: pytest.MonkeyPatch, refusal: type[Exception]) -> None:
    def refuse(self: TenantContainers, scope: StoreScope) -> OwnedChild:
        raise refusal(scope.tenant)

    monkeypatch.setattr(TenantContainers, "open", refuse)


@pytest.mark.parametrize("refusal", [UnknownTenant, TenantClosed])
def test_a_failed_open_answers_404_to_a_person_and_to_a_runner(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch, refusal: type[Exception]
) -> None:
    tenant = default_tenant(hub.engine)
    token = seed_runner(hub.open_services(tenant), "rn-open", register=False)
    _refusing_open(monkeypatch, refusal)
    client = _client(hub)

    person = client.get("/api/chunks", headers={TENANT_HEADER: tenant})
    runner = client.post(
        "/api/fleet/runners", json={"workspace_id": "w1", "name": "r"}, headers={"Authorization": f"Bearer {token}"}
    )

    for response in (person, runner):
        assert (response.status_code, response.json()["type"]) == (404, TENANT_NOT_FOUND)


def test_a_deleted_tenant_leaves_no_scope_closes_its_clients_once_and_refuses_the_next_request(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = _created(hub)
    client = _client(hub)
    assert client.get("/scratch/bound", headers={TENANT_HEADER: tenant}).status_code == 200
    before = _containers(hub).open(StoreScope(tenant))
    closed: list[int] = []
    original = ConfigObjectCache.close

    def counting(self: ConfigObjectCache) -> None:
        closed.append(id(self))
        original(self)

    monkeypatch.setattr(ConfigObjectCache, "close", counting)
    hub.open_services(tenant)  # serve the tenant, building the configured clients its deletion closes

    hub.hub_services.tenant_teardown.delete(tenant, by="usr_deleter")
    (deleted,) = closed
    hub.hub_services.close_tenant_objects()

    with pytest.raises(TenantClosed):
        before.resolver.get(StoreScope)
    with pytest.raises((TenantClosed, UnknownTenant)):
        _containers(hub).open(StoreScope(tenant))
    assert closed.count(deleted) == 1
    response = client.get("/api/chunks", headers={TENANT_HEADER: tenant})
    assert (response.status_code, response.json()["type"]) == (404, TENANT_NOT_FOUND)


def test_completing_or_failing_a_request_leaves_its_tenant_open(hub: HubHarness) -> None:
    tenant = default_tenant(hub.engine)
    client = _client(hub)
    assert client.get("/scratch/bound", headers={TENANT_HEADER: tenant}).status_code == 200
    opened = _containers(hub).open(StoreScope(tenant))

    assert client.get("/scratch/fails", headers={TENANT_HEADER: tenant}).status_code == 500

    assert _containers(hub).open(StoreScope(tenant)) is opened
    assert opened.resolver.get(StoreScope) == StoreScope(tenant)


def test_a_cancelled_request_closes_its_request_scope_and_leaves_its_tenant_open(hub: HubHarness) -> None:
    tenant = default_tenant(hub.engine)
    assert hub.app is not None
    request = Request({"type": "http", "app": hub.app, "headers": []})
    admitted = AdmittedTenant(identity=implicit_operator(tenant), scope=StoreScope(tenant), name="default")
    request.state.dishka_container = hub.app.state.hub_process.resolver

    dependency = tenant_request(request, admitted)
    assert next(dependency) is admitted
    child = request_resolver(request)
    dependency.close()

    with pytest.raises(Exception, match="request"):
        child.get(StoreScope)
    assert _containers(hub).open(StoreScope(tenant)).resolver.get(StoreScope) == StoreScope(tenant)


def test_the_bound_tenant_joins_the_log_context_and_the_request_resolves_through_its_scope(hub: HubHarness) -> None:
    tenant = default_tenant(hub.engine)

    response = _client(hub).get("/scratch/bound", headers={TENANT_HEADER: tenant})

    assert response.json() == {"log_tenant": tenant, "state_tenant": tenant, "request_scope": True}
