"""The permit routes over a fully wired hub and a migrated store (component tier)."""

# exemplar: bzh:domain-orchestration-split. Production mounts the router in ``create_app`` and registers the area in
# the process root; ``hub`` mounts it over ``build_hub``'s app and serves every request through the stand-in root.

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.tenancy import StoreScope
from exemplar.hub.api import permits
from exemplar.hub.api.permit_params import path_permit
from exemplar.hub.domain.permits.model import IReadPermitRepository
from exemplar.hub.process_root import open_store_backed
from tests.support.scaffolding import (
    TEST_HUB_ACTIONS,
    HubHarness,
    build_hub,
    default_project,
    default_tenant,
    seed_runner,
    seed_session,
    seed_user,
)

_PERMITS = importlib.import_module("exemplar.hub.store.migrations.versions.20261015_0900_permits")
_PERMIT_REVISION = importlib.import_module("exemplar.hub.store.migrations.versions.20261103_0900_permit_revision")


@pytest.fixture
def hub(tmp_path: Path) -> Iterator[HubHarness]:
    yield from _mounted(build_hub(tmp_path))


@pytest.fixture
def oauth_hub(tmp_path: Path) -> Iterator[HubHarness]:
    yield from _mounted(build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH))


def _mounted(hub: HubHarness) -> Iterator[HubHarness]:
    assert hub.app is not None and hub.config is not None
    with hub.engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _PERMITS.upgrade()
        _PERMIT_REVISION.upgrade()
    process = open_store_backed(
        hub.resolve_hub(HubStoreConnections),
        clock=hub.clock,
        actions=TEST_HUB_ACTIONS,
        config=hub.config,
        platform_tracing=DisabledPlatformTracing(),
        environ={},
    )
    hub.app.state.hub_process = process
    hub.app.include_router(permits.router)
    routes = hub.app.router.routes
    routes.insert(0, routes.pop())  # ahead of the embedded frontend's catch-all
    try:
        yield hub
    finally:
        process.close()


def _request(hub: HubHarness, holder: str = "ada", capability: str = "deploy") -> dict[str, Any]:
    resp = hub.client.post("/api/permits", json={"holder": holder, "capability": capability})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_request_answers_201_in_the_only_project_with_its_labels_and_legal_verbs(hub: HubHarness) -> None:

    view = _request(hub)

    assert view["project_id"] == default_project(hub.engine)
    assert view["project_slug"] is not None
    assert (view["holder"], view["capability"], view["state"]) == ("ada", "deploy", "requested")
    assert view["verbs"] == ["grant", "revoke"]


def test_a_repeated_request_answers_409(hub: HubHarness) -> None:
    _request(hub)

    resp = hub.client.post("/api/permits", json={"holder": "ada", "capability": "deploy"})

    assert resp.status_code == 409
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert (body["type"], body["status"], body["instance"]) == (
        "urn:blizzard:problem:permit-already-requested",
        409,
        "/api/permits",
    )


def test_an_unknown_permit_answers_404_on_read_and_on_a_verb(hub: HubHarness) -> None:

    assert hub.client.get("/api/permits/pm_missing").status_code == 404
    assert hub.client.post("/api/permits/pm_missing/grant").status_code == 404


def test_each_verb_moves_the_state_and_the_view_tracks_its_legal_verbs(hub: HubHarness) -> None:
    permit_id = _request(hub)["permit_id"]

    granted = hub.client.post(f"/api/permits/{permit_id}/grant").json()
    suspended = hub.client.post(f"/api/permits/{permit_id}/suspend").json()
    resumed = hub.client.post(f"/api/permits/{permit_id}/resume").json()
    revoked = hub.client.post(f"/api/permits/{permit_id}/revoke", json={"note": " left the team "}).json()

    assert (granted["state"], granted["verbs"]) == ("granted", ["suspend", "revoke"])
    assert (suspended["state"], suspended["verbs"]) == ("suspended", ["resume", "revoke"])
    assert (resumed["state"], resumed["verbs"]) == ("granted", ["suspend", "revoke"])
    assert (revoked["state"], revoked["verbs"], revoked["revocation_note"]) == ("revoked", [], "left the team")


def test_a_verb_the_state_refuses_answers_409(hub: HubHarness) -> None:
    permit_id = _request(hub)["permit_id"]

    assert hub.client.post(f"/api/permits/{permit_id}/resume").status_code == 409


def test_a_verb_decided_on_a_copy_another_write_moved_answers_409_and_lands_nothing(hub: HubHarness) -> None:
    """One operator grants the requested permit; the other's revoke, resolved before that grant landed, is refused."""
    assert hub.app is not None
    permit_id = _request(hub)["permit_id"]
    tenant = hub.app.state.hub_process.tenants.open(StoreScope(default_tenant(hub.engine)))
    read_before = tenant.resolver.get(IReadPermitRepository).get(permit_id)
    assert hub.client.post(f"/api/permits/{permit_id}/grant").status_code == 200
    hub.app.dependency_overrides[path_permit] = lambda: read_before

    resp = hub.client.post(f"/api/permits/{permit_id}/revoke", json={"note": "left the team"})

    del hub.app.dependency_overrides[path_permit]
    assert resp.status_code == 409
    assert hub.client.get(f"/api/permits/{permit_id}").json()["state"] == "granted"


def test_a_blank_revocation_note_answers_422_and_records_nothing(hub: HubHarness) -> None:
    permit_id = _request(hub)["permit_id"]

    resp = hub.client.post(f"/api/permits/{permit_id}/revoke", json={"note": "   "})

    assert resp.status_code == 422
    assert hub.client.get(f"/api/permits/{permit_id}").json()["state"] == "requested"


def test_the_list_pages_by_id_descending_at_one_instant_through_next_cursor(hub: HubHarness) -> None:
    ids = [_request(hub, capability=f"cap-{n}")["permit_id"] for n in range(3)]

    first = hub.client.get("/api/permits", params={"limit": 2}).json()
    second = hub.client.get("/api/permits", params={"limit": 2, "cursor": first["next_cursor"]}).json()

    assert [p["permit_id"] for p in first["permits"] + second["permits"]] == sorted(ids, reverse=True)
    assert second["next_cursor"] is None


def test_a_malformed_cursor_answers_422_malformed_cursor(hub: HubHarness) -> None:

    resp = hub.client.get("/api/permits", params={"cursor": "not-a-cursor"})

    assert resp.status_code == 422
    assert resp.json() == {"detail": "malformed cursor"}


def test_a_runner_token_answers_403_on_every_permit_route(hub: HubHarness) -> None:
    permit_id = _request(hub)["permit_id"]
    runner = {"Authorization": f"Bearer {seed_runner(hub, 'r1')}"}

    assert hub.client.get("/api/permits", headers=runner).status_code == 403
    assert hub.client.get(f"/api/permits/{permit_id}", headers=runner).status_code == 403
    assert hub.client.post(f"/api/permits/{permit_id}/grant", headers=runner).status_code == 403


def test_a_holders_live_permits_answer_through_the_area_wired_reader_and_skip_the_revoked(hub: HubHarness) -> None:
    kept = _request(hub, capability="deploy")["permit_id"]
    revoked = _request(hub, capability="rollback")["permit_id"]
    _request(hub, holder="grace")
    hub.client.post(f"/api/permits/{revoked}/revoke", json={"note": "left the team"})

    resp = hub.client.get("/api/holders/ada/permits")

    assert resp.status_code == 200
    assert [p["permit_id"] for p in resp.json()["permits"]] == [kept]


def test_a_signed_in_member_reaches_both_permit_services_through_the_authenticated_route(
    oauth_hub: HubHarness,
) -> None:
    """Under session auth, a member's request resolves the permit verbs and its holder read the holder reader, each
    through the admitted tenant's request scope; a signed-out caller is refused before either resolves."""
    assert oauth_hub.app is not None
    member = seed_user(oauth_hub, username="ada", role=Role.ADMIN)
    signed_in = {"Authorization": f"Bearer {seed_session(oauth_hub, member)}"}
    client = TestClient(oauth_hub.app)

    requested = client.post("/api/permits", json={"holder": "ada", "capability": "deploy"}, headers=signed_in)
    held = client.get("/api/holders/ada/permits", headers=signed_in)
    signed_out = client.get("/api/holders/ada/permits")

    assert requested.status_code == 201, requested.text
    assert [p["permit_id"] for p in held.json()["permits"]] == [requested.json()["permit_id"]]
    assert signed_out.status_code == 401
