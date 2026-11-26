"""Helpers for the store work source registry tests, shared with other test modules."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import httpx2
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.hub.domain.config.work_sources import IReadWorkSourceRepository
from tests.support.scaffolding import (
    ForgeDouble,
    ForgeIssue,
    bound_socket,
    ensure_fixture_forge,
    github_double,
    tenant_adapter_of,
)

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
    app: FastAPI,
    forge_url: str,
    *,
    annotate: bool = False,
    slug: str = "widget",
    locator: str = "acme/widget",
) -> str:
    """Create and link the source in ``app``'s only open tenant, returning its id."""
    if client.get("/api/secrets/gh").status_code == 404:
        assert client.post("/api/secrets", json={"name": "gh", "value": "tok-a"}).status_code == 201
    created = client.post(
        "/api/work-sources",
        json={
            "slug": slug,
            "provider": "github",
            "locator": locator,
            "forge": ensure_fixture_forge(client, forge_url),
            "annotate": annotate,
            "secret": "gh",
        },
    )
    assert created.status_code == 201, created.text
    source_id = _id(app, slug)
    linked = client.put(f"/api/projects/default/source-links/{source_id}")
    assert linked.status_code == 200, linked.text
    return source_id


def _ingest(client: TestClient, token: str) -> httpx2.Response:
    return client.post("/api/chunks", json={"tokens": [token]})


def promote(client: TestClient, token: str) -> None:
    resp = _ingest(client, token)
    assert resp.status_code == 201, resp.text
    assert client.post(f"/api/chunks/{resp.json()['chunk_id']}/promote").status_code == 202


def _id(app: FastAPI, slug: str) -> str:
    record = tenant_adapter_of(app, IReadWorkSourceRepository).resolve(slug)
    assert record is not None
    return record.work_source_id
