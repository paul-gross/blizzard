"""Helpers for the store work source registry tests, shared with other test modules."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import httpx2
import pytest
import uvicorn
from fastapi.testclient import TestClient

from blizzard.hub.composition import HubServices, TenantServices
from blizzard.hub.tenancy import StoreScope
from tests.support import ForgeDouble, ForgeIssue, bound_socket, github_double

_ISSUES: dict[str, ForgeIssue] = {
    f"acme/widget#{n}": {"title": f"t{n}", "body": f"b{n}", "comments": []} for n in (1, 2)
}


@pytest.fixture
def forge() -> Iterator[tuple[ForgeDouble, str]]:
    """The double and the loopback URL it is served on."""
    double = github_double(issues={key: value.copy() for key, value in _ISSUES.items()})
    sock = bound_socket()
    server = uvicorn.Server(uvicorn.Config(double.app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    deadline = time.monotonic() + 10.0
    while not server.started:
        assert time.monotonic() < deadline, "forge double never came up"
        time.sleep(0.02)
    try:
        yield double, url
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        sock.close()


def create(
    client: TestClient,
    services: HubServices,
    forge_url: str,
    *,
    annotate: bool = False,
    slug: str = "widget",
    locator: str = "acme/widget",
) -> str:
    """Create and link the source, returning its id."""
    if client.get("/api/secrets/gh").status_code == 404:
        assert client.post("/api/secrets", json={"name": "gh", "value": "tok-a"}).status_code == 201
    created = client.post(
        "/api/work-sources",
        json={
            "slug": slug,
            "provider": "github",
            "locator": locator,
            "api_base": forge_url,
            "annotate": annotate,
            "secret": "gh",
        },
    )
    assert created.status_code == 201, created.text
    source_id = _id(services, slug)
    linked = client.put(f"/api/projects/default/source-links/{source_id}")
    assert linked.status_code == 200, linked.text
    return source_id


def _ingest(client: TestClient, token: str) -> httpx2.Response:
    return client.post("/api/chunks", json={"tokens": [token]})


def promote(client: TestClient, token: str) -> None:
    resp = _ingest(client, token)
    assert resp.status_code == 201, resp.text
    assert client.post(f"/api/chunks/{resp.json()['chunk_id']}/promote").status_code == 202


def _tenant(services: HubServices) -> TenantServices:
    tenant = services.tenants.open_tenants.only()
    assert tenant is not None
    return services.tenants.open_services(StoreScope(tenant))


def _id(services: HubServices, slug: str) -> str:
    record = _tenant(services).work_source_records.resolve(slug)
    assert record is not None
    return record.work_source_id
