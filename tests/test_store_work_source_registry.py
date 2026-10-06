"""The hosted hub's work sources read from their records on use (component tier).

A real composition (``build_hosted_app``) over a migrated store, with the GitHub-shaped
double served on a loopback port so the adapter the factory builds talks HTTP to it.
Sources and secrets are written through the hub's own API while the app runs; nothing is
rebuilt between a write and the next registry call."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.work_sources.annotator import WorkStatusMarker
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from tests.support import forge_state, free_port, github_double

pytestmark = pytest.mark.component

_ISSUES = {f"acme/widget#{n}": {"title": f"t{n}", "body": f"b{n}", "comments": []} for n in (1, 2)}


@pytest.fixture
def forge() -> Iterator[tuple[TestClient, str]]:
    """The double and the loopback URL it is served on."""
    double = github_double(issues={key: dict(value) for key, value in _ISSUES.items()})
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(double.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    while not server.started:
        assert time.monotonic() < deadline, "forge double never came up"
        time.sleep(0.02)
    try:
        yield double, url
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[tuple[TestClient, HubServices]]:
    config = hub_runtime.init_environment(tmp_path / "hub")
    app = hub_app.build_hosted_app(config)
    # Never entered as a context manager: no lifespan, so no background sweep races the test's own passes.
    client = TestClient(app)
    yield client, app.state.services
    app.state.engine.dispose()


def _create(client: TestClient, forge_url: str, *, annotate: bool = False) -> None:
    assert client.post("/api/secrets", json={"name": "gh", "value": "tok-a"}).status_code == 201
    created = client.post(
        "/api/work-sources",
        json={
            "name": "widget",
            "provider": "github",
            "locator": "acme/widget",
            "api_base": forge_url,
            "annotate": annotate,
            "secret": "gh",
        },
    )
    assert created.status_code == 201, created.text


def _ingest(client: TestClient, token: str) -> httpx.Response:
    return client.post("/api/chunks", json={"tokens": [token]})


def _promote(client: TestClient, token: str) -> None:
    resp = _ingest(client, token)
    assert resp.status_code == 201, resp.text
    assert client.post(f"/api/chunks/{resp.json()['chunk_id']}/promote").status_code == 202


def _adapter(services: HubServices) -> GitHubWorkSource:
    source = services.work_sources.get("widget")
    assert isinstance(source, GitHubWorkSource)
    return source


def test_a_source_created_mid_run_claims_ingest_with_no_rebuild(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    _, forge_url = forge
    assert _ingest(client, "widget:1").status_code == 422

    _create(client, forge_url)

    assert _ingest(client, "widget:1").status_code == 201
    assert services.work_sources.names() == ["widget", "hub"]
    assert services.work_sources.get("widget") is not None


def test_a_secret_replace_reaches_the_next_get_and_a_later_one_closes_the_replaced_client(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    _create(client, forge[1])
    before = _adapter(services)
    assert before._client.headers["Authorization"] == "token tok-a"
    assert _adapter(services) is before  # a hit reuses the built adapter

    assert client.put("/api/secrets/gh/value", json={"value": "tok-b"}).status_code == 200

    after = _adapter(services)
    assert after._client.headers["Authorization"] == "token tok-b"
    assert not before._client.is_closed  # a caller may still be mid-request on it
    assert client.put("/api/secrets/gh/value", json={"value": "tok-c"}).status_code == 200
    _adapter(services)
    assert before._client.is_closed


def test_a_locator_edit_reaches_the_next_resolve(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    _create(client, forge[1])
    old_url = "https://github.com/acme/widget/issues/7"
    new_url = "https://github.com/acme/gadget/issues/7"
    assert services.work_sources.resolve(old_url) == WorkRef(source="widget", ref="7")

    assert client.patch("/api/work-sources/widget", json={"locator": "acme/gadget"}).status_code == 200

    assert services.work_sources.resolve(old_url) is None
    assert services.work_sources.resolve(new_url) == WorkRef(source="widget", ref="7")


def test_the_annotation_sweep_reads_the_annotating_set_each_pass(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    double, forge_url = forge
    _create(client, forge_url)
    _promote(client, "widget:1")
    assert services.annotation is not None

    services.annotation.sweep()
    assert forge_state(double)["issue_labels"] == {}

    assert client.patch("/api/work-sources/widget", json={"annotate": True}).status_code == 200
    services.annotation.sweep()
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:ingested"}  # type: ignore[index]

    assert client.patch("/api/work-sources/widget", json={"annotate": False}).status_code == 200
    services.annotation.sweep()
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == set()  # type: ignore[index]


def test_a_retired_source_refuses_ingest_while_its_ingested_item_still_closes_and_annotates(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    double, forge_url = forge
    _create(client, forge_url, annotate=True)
    _promote(client, "widget:1")

    assert client.post("/api/work-sources/widget/retire").status_code == 200

    refused = _ingest(client, "widget:2")
    assert refused.status_code == 422
    assert services.work_sources.names() == ["hub"]
    assert services.work_sources.annotating_names() == []
    closer = services.work_sources.closer("widget")
    annotator = services.work_sources.annotator("widget")
    assert closer is not None and annotator is not None
    pointer = WorkRef(source="widget", ref="1")
    annotator.set_status(pointer, WorkStatusMarker.IN_PROGRESS)
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:in-progress"}  # type: ignore[index]
    closer.close(pointer, trace=None)
    assert forge_state(double)["issue_state"]["acme/widget#1"]["state"] == "closed"  # type: ignore[index]


def test_a_retired_source_whose_secret_is_retired_reads_as_absent_not_as_an_error(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    _create(client, forge[1])
    assert client.post("/api/work-sources/widget/retire").status_code == 200
    assert client.post("/api/secrets/gh/retire").status_code == 200

    assert services.work_sources.get("widget") is None
    assert services.work_sources.closer("widget") is None
    assert services.work_sources.annotator("widget") is None
    assert services.work_sources.label_clearer("widget") is None
