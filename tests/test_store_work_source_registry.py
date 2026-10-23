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

import httpx2
import pytest
import uvicorn
from fastapi.testclient import TestClient

from blizzard.hub.composition import HubServices, TenantServices
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.config.work_sources import ConfigFieldError, WorkSourceEdit, WorkSourceSlug
from blizzard.hub.tenancy import StoreScope
from blizzard.hub.work_sources.annotator import WorkStatusMarker
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.source import IWorkSourceRegistry
from tests.config_inputs import build_hosted_hub_app, init_hub
from tests.support import OP, forge_state, free_port, github_double

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
    config = init_hub(tmp_path / "hub")
    app = build_hosted_hub_app(config)
    # Never entered as a context manager: no lifespan, so no background sweep races the test's own passes.
    client = TestClient(app)
    yield client, app.state.services
    app.state.engine.dispose()


def _create(
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


def _promote(client: TestClient, token: str) -> None:
    resp = _ingest(client, token)
    assert resp.status_code == 201, resp.text
    assert client.post(f"/api/chunks/{resp.json()['chunk_id']}/promote").status_code == 202


def _tenant(services: HubServices) -> TenantServices:
    tenant = services.tenants.open_tenants.only()
    assert tenant is not None
    return services.tenants.open_services(StoreScope(tenant))


def _sources(services: HubServices) -> IWorkSourceRegistry:
    return _tenant(services).work_sources


def _id(services: HubServices, slug: str) -> str:
    record = _tenant(services).work_source_records.resolve(slug)
    assert record is not None
    return record.work_source_id


def _rename(services: HubServices, source_id: str, slug: str) -> None:
    tenant = _tenant(services)
    record = tenant.work_source_records.resolve(source_id)
    assert record is not None
    tenant.config_authoring.edit_work_source(record, WorkSourceEdit(slug=slug), OP)


def _adapter(services: HubServices) -> GitHubWorkSource:
    source = _sources(services).get(_id(services, "widget"))
    assert isinstance(source, GitHubWorkSource)
    return source


def test_a_source_created_mid_run_claims_ingest_with_no_rebuild(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    _, forge_url = forge
    assert _ingest(client, "widget:1").status_code == 422

    source_id = _create(client, services, forge_url)

    assert _ingest(client, "widget:1").status_code == 201
    assert _sources(services).names() == ["widget", "hub"]
    assert _sources(services).get(source_id) is not None


def test_a_secret_replace_reaches_the_next_get_and_a_later_one_closes_the_replaced_client(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    _create(client, services, forge[1])
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
    source_id = _create(client, services, forge[1])
    old_url = "https://github.com/acme/widget/issues/7"
    new_url = "https://github.com/acme/gadget/issues/7"
    assert _sources(services).resolve(old_url) == WorkRef(source=source_id, ref="7")

    assert client.patch(f"/api/work-sources/{source_id}", json={"locator": "acme/gadget"}).status_code == 200

    assert _sources(services).resolve(old_url) is None
    assert _sources(services).resolve(new_url) == WorkRef(source=source_id, ref="7")


def test_the_annotation_sweep_reads_the_annotating_set_each_pass(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    double, forge_url = forge
    source_id = _create(client, services, forge_url)
    _promote(client, "widget:1")
    assert services.annotation is not None

    services.annotation.sweep()
    assert forge_state(double)["issue_labels"] == {}

    assert client.patch(f"/api/work-sources/{source_id}", json={"annotate": True}).status_code == 200
    services.annotation.sweep()
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:ingested"}  # type: ignore[index]

    assert client.patch(f"/api/work-sources/{source_id}", json={"annotate": False}).status_code == 200
    services.annotation.sweep()
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == set()  # type: ignore[index]


def test_a_retired_source_refuses_ingest_while_its_ingested_item_still_closes_and_annotates(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    double, forge_url = forge
    source_id = _create(client, services, forge_url, annotate=True)
    _promote(client, "widget:1")

    assert client.post(f"/api/work-sources/{source_id}/retire").status_code == 200

    refused = _ingest(client, "widget:2")
    assert refused.status_code == 422
    assert _sources(services).names() == ["hub"]
    assert _sources(services).annotating_ids() == []
    closer = _sources(services).closer(source_id)
    annotator = _sources(services).annotator(source_id)
    assert closer is not None and annotator is not None
    pointer = WorkRef(source=source_id, ref="1")
    annotator.set_status(pointer, WorkStatusMarker.IN_PROGRESS)
    assert forge_state(double)["issue_labels"]["acme/widget#1"] == {"blizzard:in-progress"}  # type: ignore[index]
    closer.close(pointer, trace=None)
    assert forge_state(double)["issue_state"]["acme/widget#1"]["state"] == "closed"  # type: ignore[index]


def test_a_retired_source_whose_secret_is_retired_reads_as_absent_not_as_an_error(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    source_id = _create(client, services, forge[1])
    assert client.post(f"/api/work-sources/{source_id}/retire").status_code == 200
    assert client.post("/api/secrets/gh/retire").status_code == 200

    assert _sources(services).get(source_id) is None
    assert _sources(services).closer(source_id) is None
    assert _sources(services).annotator(source_id) is None
    assert _sources(services).label_clearer(source_id) is None
    # The record still names the source's current slug, so the item it held still displays.
    assert _sources(services).label(WorkRef(source=source_id, ref="1")) == "widget#1"


# --------------------------------------------------------------------------- #
# Identity: refs hold the id, tokens may name any handle, labels follow a rename.


def test_an_ingested_ref_holds_the_id_whichever_handle_the_token_names(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    source_id = _create(client, services, forge[1])
    sources = _sources(services)

    assert sources.resolve("widget:1") == WorkRef(source=source_id, ref="1")
    assert sources.resolve("widget#1") == WorkRef(source=source_id, ref="1")
    assert sources.resolve(f"{source_id}:1") == WorkRef(source=source_id, ref="1")
    assert sources.resolve("https://github.com/acme/widget/issues/1") == WorkRef(source=source_id, ref="1")
    assert sources.resolve("hub:1") == WorkRef(source="hub", ref="1")


def test_a_rename_keeps_ingested_refs_resolving_and_their_labels_follow(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    source_id = _create(client, services, forge[1])
    minted = _ingest(client, "widget:1")
    assert minted.status_code == 201, minted.text
    chunk_id = minted.json()["chunk_id"]

    _rename(services, source_id, "widget-core")

    [ref] = client.get(f"/api/chunks/{chunk_id}").json()["work_refs"]
    assert ref["source"] == source_id
    assert ref["label"] == "widget-core#1"
    assert _sources(services).label(WorkRef(source=source_id, ref="1")) == "widget-core#1"
    # Both the new slug and the one the source left still name it.
    assert _sources(services).resolve("widget-core:2") == WorkRef(source=source_id, ref="2")
    assert _sources(services).resolve("widget:2") == WorkRef(source=source_id, ref="2")
    assert _sources(services).names() == ["widget-core", "hub"]
    # Re-ingesting the held item under its former slug is refused naming its current label.
    held = _ingest(client, "widget:1")
    assert held.status_code == 409
    assert held.json()["source"] == source_id


def test_a_reclaimed_former_slug_resolves_to_its_new_holder(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    first = _create(client, services, forge[1])
    _rename(services, first, "widget-core")

    second = _create(client, services, forge[1], slug="widget", locator="acme/gadget")

    assert second != first
    assert _sources(services).resolve("widget:3") == WorkRef(source=second, ref="3")
    assert _sources(services).resolve("widget-core:3") == WorkRef(source=first, ref="3")


def test_an_id_shaped_prefix_never_resolves_as_a_slug(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    """A ``wsrc_`` value is only ever an id: one naming no source resolves to nothing, whatever the slugs."""
    client, services = hub
    source_id = _create(client, services, forge[1])
    unknown = "wsrc_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert unknown != source_id

    assert _sources(services).resolve(f"{unknown}:1") is None
    assert WorkSourceSlug.of(source_id) is None


def test_hub_is_never_a_work_source_slug(hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]) -> None:
    client, services = hub
    source_id = _create(client, services, forge[1])

    with pytest.raises(ConfigFieldError):
        _rename(services, source_id, "hub")
    # A ``hub`` prefix always reaches the built-in source, never a configured one.
    assert _sources(services).resolve("hub:4") == WorkRef(source="hub", ref="4")


def test_a_retired_source_s_former_slug_is_refused_like_its_current_one(
    hub: tuple[TestClient, HubServices], forge: tuple[TestClient, str]
) -> None:
    client, services = hub
    source_id = _create(client, services, forge[1])
    _rename(services, source_id, "widget-core")
    assert client.post(f"/api/work-sources/{source_id}/retire").status_code == 200

    assert _sources(services).resolve("widget:1") is None
    assert _sources(services).resolve("widget-core:1") is None
    assert _sources(services).resolve(f"{source_id}:1") is None
