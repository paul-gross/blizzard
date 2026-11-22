"""The finding routes resolve their services through the admitted tenant's request scope (component tier).

Every refusal the tenant chain answers before a finding route runs keeps the payload an unmigrated route on the same
chain answers; a tenant deleted between its admission and a finding service's lazy resolution answers as a closed
tenant, never as a server fault; concurrent requests share only their own tenant's services; and an async route's
first construction runs off the event loop, as a sync route's does."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Any, cast

import pytest
from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues
from blizzard.hub.api.auth import require_runner_principal
from blizzard.hub.api.auth_session import TENANT_HEADER, AdmittedTenant, tenant_bound, tenant_request
from blizzard.hub.api.resolution import request_resolver
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.domain.garden.findings.model import FindingExitService, IReadFindingRepository
from blizzard.hub.scopes.tenants import TenantContainers
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    close_tenant,
    default_tenant,
    seed_membership,
    seed_runner,
    seed_session,
    seed_user,
)

#: An unmigrated route on the finding routes' own chain — the people guards and ``FLEET_VIEW`` over ``Services``.
_UNMIGRATED = "/api/garden-proposals"
_UNKNOWN = TenantId("ten_00000000000000000000000000")


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _tenant_exits(request: Request, _tenant: Annotated[AdmittedTenant, Depends(tenant_request)]) -> int:
    """A synchronous dependency resolving a tenant service through the request's scope."""
    exits = request_resolver(request).get(FindingExitService)
    request.app.state.scratch_constructed.append(("dependency", _on_loop()))
    return id(exits)


#: The tenant service's identity, resolved by :func:`_tenant_exits`.
_Exits = Annotated[int, Depends(_tenant_exits)]


def _scratch_router() -> APIRouter:
    people = APIRouter(prefix="/scratch", dependencies=[Depends(tenant_bound)])
    fleet = APIRouter(prefix="/scratch/fleet", dependencies=[Depends(require_runner_principal), Depends(tenant_bound)])

    @people.get("/findings")
    @inject_sync
    def people_findings(findings: FromDishka[IReadFindingRepository], request: Request) -> dict[str, object]:
        return {"tenant": request.state.tenant_id, "findings": id(findings)}

    @fleet.get("/findings")
    @inject_sync
    def fleet_findings(findings: FromDishka[IReadFindingRepository]) -> dict[str, object]:
        return {"findings": id(findings)}

    @people.get("/async")
    @inject_sync
    async def async_route(reads: FromDishka[HubScopedReads], exits: _Exits, request: Request) -> dict[str, object]:
        return {
            "reads": id(reads),
            "exits": exits,
            "eager": reads is request.app.state.hub_process.resolver.get(HubScopedReads),
        }

    combined = APIRouter()
    combined.include_router(people)
    combined.include_router(fleet)
    return combined


@pytest.fixture
def constructed() -> list[tuple[str, bool]]:
    return []


@pytest.fixture
def hub(tmp_path: Path, constructed: list[tuple[str, bool]]) -> HubHarness:
    harness = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    assert harness.app is not None
    harness.app.state.scratch_constructed = constructed
    harness.app.include_router(_scratch_router())
    harness.app.router.routes.sort(key=lambda route: getattr(route, "name", None) == "web")
    return harness


def _client(hub: HubHarness) -> TestClient:
    assert hub.app is not None
    return TestClient(hub.app, raise_server_exceptions=False)


def _session(hub: HubHarness, username: str, role: Role, *members_of: TenantId) -> dict[str, str]:
    """A person's bearer header: ``role`` in the default tenant, and an admin membership in each of ``members_of``."""
    user = seed_user(hub, username=username, role=role)
    for tenant in members_of:
        seed_membership(hub.engine, tenant, user.user_id, Role.ADMIN)
    return {"Authorization": f"Bearer {seed_session(hub, user)}"}


def _answer(client: TestClient, method: str, path: str, headers: dict[str, str]) -> tuple[int, object]:
    response = client.request(method, path, headers=headers, json={"finding_ids": ["fin_x"], "note": "n"})
    body: object = response.json()
    if isinstance(body, dict):
        return response.status_code, {
            key: value for key, value in cast("dict[str, object]", body).items() if key != "instance"
        }
    return response.status_code, body


def _closed(hub: HubHarness) -> TenantId:
    closed = hub.add_tenant("gone")
    close_tenant(hub.engine, closed.tenant)
    hub.hub_services.tenants.open_tenants.refresh()
    return closed.tenant


def _refusal_headers(hub: HubHarness, refusal: str) -> dict[str, str]:
    default = default_tenant(hub.engine)
    if refusal == "missing identity":
        return {TENANT_HEADER: default}
    if refusal == "machine tenant mismatch":
        token = seed_runner(hub.open_services(default), "rn-mismatch", register=False)
        return {"Authorization": f"Bearer {token}", TENANT_HEADER: hub.add_tenant("other").tenant}
    if refusal == "missing membership":
        return {**_session(hub, "ada", Role.ADMIN), TENANT_HEADER: hub.add_tenant("other").tenant}
    if refusal == "unknown tenant":
        return {**_session(hub, "ada", Role.ADMIN), TENANT_HEADER: _UNKNOWN}
    if refusal == "closed tenant":
        return {**_session(hub, "ada", Role.ADMIN), TENANT_HEADER: _closed(hub)}
    assert refusal == "route admission"
    return {**_session(hub, "gus", Role.GUEST), TENANT_HEADER: default}


@pytest.mark.parametrize(
    "refusal",
    [
        "missing identity",
        "machine tenant mismatch",
        "missing membership",
        "unknown tenant",
        "closed tenant",
        "route admission",
    ],
)
def test_a_finding_route_keeps_the_refusal_an_unmigrated_route_on_its_chain_answers(
    hub: HubHarness, refusal: str
) -> None:
    headers = _refusal_headers(hub, refusal)
    client = _client(hub)

    if refusal == "route admission":
        # A guest views findings but may not exit one: the write's own permission refuses it.
        assert _answer(client, "GET", "/api/findings", headers)[0] == 200
        status, body = _answer(client, "POST", "/api/findings/resolve", headers)
        _, unmigrated = _answer(client, "POST", "/api/garden-proposals/gpr_x/pass", headers)
        assert status == 403
        assert body == unmigrated
        return
    migrated = _answer(client, "GET", "/api/findings", headers)
    unmigrated = _answer(client, "GET", _UNMIGRATED, headers)

    assert migrated == unmigrated
    assert migrated[0] in {401, 403, 404}


def _deleting_after_admission(hub: HubHarness, monkeypatch: pytest.MonkeyPatch, tenant: TenantId) -> None:
    original = TenantContainers.request

    def admitted_then_deleted(self: TenantContainers, scope: StoreScope, values: ScopeValues) -> OwnedChild:
        child = original(self, scope, values)
        if scope.tenant == tenant:
            hub.hub_services.tenant_teardown.delete(tenant, by="usr_deleter")
        return child

    monkeypatch.setattr(TenantContainers, "request", admitted_then_deleted)


def test_a_tenant_deleted_before_a_lazy_finding_resolution_answers_a_person_404(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = hub.add_tenant("doomed")
    headers = {**_session(hub, "sue", Role.SUPERUSER, created.tenant), TENANT_HEADER: created.tenant}
    assert _client(hub).get("/scratch/findings", headers=headers).status_code == 200
    _deleting_after_admission(hub, monkeypatch, created.tenant)

    response = _client(hub).get("/scratch/findings", headers=headers)

    assert (response.status_code, response.json()) == (404, {"detail": "no such tenant"})


def test_a_tenant_deleted_before_a_lazy_finding_resolution_answers_a_runner_401(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = hub.add_tenant("doomed")
    token = seed_runner(created.services, "rn-doomed", register=False)
    headers = {"Authorization": f"Bearer {token}"}
    assert _client(hub).get("/scratch/fleet/findings", headers=headers).status_code == 200
    _deleting_after_admission(hub, monkeypatch, created.tenant)

    response = _client(hub).get("/scratch/fleet/findings", headers=headers)

    assert response.status_code == 401


def test_concurrent_requests_share_only_their_own_tenants_services(hub: HubHarness) -> None:
    default = default_tenant(hub.engine)
    second = hub.add_tenant("second").tenant
    superuser = _session(hub, "sue", Role.SUPERUSER, second)
    calls: list[dict[str, str]] = [
        {**superuser, TENANT_HEADER: default},
        {**_session(hub, "ada", Role.ADMIN), TENANT_HEADER: default},
        {**superuser, TENANT_HEADER: second},
    ] * 4
    start = threading.Barrier(len(calls))

    def call(headers: dict[str, str]) -> dict[str, Any]:
        client = _client(hub)
        start.wait(timeout=30)
        response = client.get("/scratch/findings", headers=headers)
        assert response.status_code == 200, response.text
        return response.json()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        answers = list(pool.map(call, calls))

    by_tenant: dict[str, set[int]] = {}
    for answer in answers:
        by_tenant.setdefault(answer["tenant"], set()).add(answer["findings"])
    assert by_tenant == {
        default: {id(hub.resolve(default, IReadFindingRepository))},
        second: {id(hub.resolve(second, IReadFindingRepository))},
    }


def _spy_construction(monkeypatch: pytest.MonkeyPatch, constructed: list[tuple[str, bool]]) -> None:
    original: Callable[..., None] = FindingExitService.__init__

    def spied(self: FindingExitService, **kwargs: Any) -> None:
        constructed.append(("construction", _on_loop()))
        original(self, **kwargs)

    monkeypatch.setattr(FindingExitService, "__init__", spied)


def test_an_async_routes_first_construction_and_a_sync_routes_run_off_the_event_loop(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch, constructed: list[tuple[str, bool]]
) -> None:
    async_tenant = hub.hub_services.tenant_admin.create("async", admin_user_id=None, by="usr_creator").tenant_id
    sync_tenant = hub.hub_services.tenant_admin.create("sync", admin_user_id=None, by="usr_creator").tenant_id
    superuser = _session(hub, "sue", Role.SUPERUSER, async_tenant, sync_tenant)
    _spy_construction(monkeypatch, constructed)
    client = _client(hub)

    answered = client.get("/scratch/async", headers={**superuser, TENANT_HEADER: async_tenant})
    first = list(constructed)
    resolved = client.post(
        "/api/findings/resolve",
        headers={**superuser, TENANT_HEADER: sync_tenant},
        json={"finding_ids": ["fin_x"], "note": "n"},
    )

    assert answered.status_code == 200, answered.text
    assert answered.json()["eager"] is True
    assert answered.json()["exits"] == id(hub.resolve(async_tenant, FindingExitService))
    assert ("construction", False) in first
    assert resolved.status_code == 404  # the unknown finding, after the exit service was built for the tenant
    assert constructed.count(("construction", False)) == 2
    assert all(on_loop is False for _, on_loop in constructed)
