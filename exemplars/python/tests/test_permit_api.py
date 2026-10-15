"""The permit routes over a fully wired hub and a migrated store (component tier)."""

# exemplar: bzh:domain-orchestration-split. Production mounts the router in ``create_app`` and builds the services
# in ``build_hub``; ``_hub`` mounts it over ``build_hub``'s app and serves every route the stand-in services.

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from blizzard.hub.api import deps as hub_deps
from exemplar.hub.api import deps, permits
from exemplar.hub.composition import build_tenant_services, build_tenant_stores
from tests.support import HubHarness, build_hub, default_project, default_tenant, scoped_connections, seed_runner

pytestmark = pytest.mark.component

_PERMITS = importlib.import_module("exemplar.hub.store.migrations.versions.20261015_0900_permits")


def _hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    with hub.engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _PERMITS.upgrade()
    stores = build_tenant_stores(scoped_connections(hub.engine, default_tenant(hub.engine)), clock=hub.clock)
    hub.app.state.tenant_services = build_tenant_services(stores, hub.services)
    hub.app.dependency_overrides[hub_deps.tenant_services] = deps.tenant_services
    hub.app.include_router(permits.router)
    routes = hub.app.router.routes
    routes.insert(0, routes.pop())  # ahead of the embedded frontend's catch-all
    return hub


def _request(hub: HubHarness, holder: str = "ada", capability: str = "deploy") -> dict[str, Any]:
    resp = hub.client.post("/api/permits", json={"holder": holder, "capability": capability})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_request_answers_201_in_the_only_project_with_its_labels_and_legal_verbs(tmp_path: Path) -> None:
    hub = _hub(tmp_path)

    view = _request(hub)

    assert view["project_id"] == default_project(hub.engine)
    assert view["project_slug"] is not None
    assert (view["holder"], view["capability"], view["state"]) == ("ada", "deploy", "requested")
    assert view["verbs"] == ["grant", "revoke"]


def test_a_repeated_request_answers_409(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _request(hub)

    resp = hub.client.post("/api/permits", json={"holder": "ada", "capability": "deploy"})

    assert resp.status_code == 409


def test_an_unknown_permit_answers_404_on_read_and_on_a_verb(tmp_path: Path) -> None:
    hub = _hub(tmp_path)

    assert hub.client.get("/api/permits/pm_missing").status_code == 404
    assert hub.client.post("/api/permits/pm_missing/grant").status_code == 404


def test_each_verb_moves_the_state_and_the_view_tracks_its_legal_verbs(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    permit_id = _request(hub)["permit_id"]

    granted = hub.client.post(f"/api/permits/{permit_id}/grant").json()
    suspended = hub.client.post(f"/api/permits/{permit_id}/suspend").json()
    resumed = hub.client.post(f"/api/permits/{permit_id}/resume").json()
    revoked = hub.client.post(f"/api/permits/{permit_id}/revoke", json={"note": " left the team "}).json()

    assert (granted["state"], granted["verbs"]) == ("granted", ["suspend", "revoke"])
    assert (suspended["state"], suspended["verbs"]) == ("suspended", ["resume", "revoke"])
    assert (resumed["state"], resumed["verbs"]) == ("granted", ["suspend", "revoke"])
    assert (revoked["state"], revoked["verbs"], revoked["revocation_note"]) == ("revoked", [], "left the team")


def test_a_verb_the_state_refuses_answers_409(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    permit_id = _request(hub)["permit_id"]

    assert hub.client.post(f"/api/permits/{permit_id}/resume").status_code == 409


def test_a_blank_revocation_note_answers_422_and_records_nothing(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    permit_id = _request(hub)["permit_id"]

    resp = hub.client.post(f"/api/permits/{permit_id}/revoke", json={"note": "   "})

    assert resp.status_code == 422
    assert hub.client.get(f"/api/permits/{permit_id}").json()["state"] == "requested"


def test_the_list_pages_by_id_descending_at_one_instant_through_next_cursor(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    ids = [_request(hub, capability=f"cap-{n}")["permit_id"] for n in range(3)]

    first = hub.client.get("/api/permits", params={"limit": 2}).json()
    second = hub.client.get("/api/permits", params={"limit": 2, "cursor": first["next_cursor"]}).json()

    assert [p["permit_id"] for p in first["permits"] + second["permits"]] == sorted(ids, reverse=True)
    assert second["next_cursor"] is None


def test_a_malformed_cursor_answers_422_malformed_cursor(tmp_path: Path) -> None:
    hub = _hub(tmp_path)

    resp = hub.client.get("/api/permits", params={"cursor": "not-a-cursor"})

    assert resp.status_code == 422
    assert resp.json() == {"detail": "malformed cursor"}


def test_a_runner_token_answers_403_on_every_permit_route(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    permit_id = _request(hub)["permit_id"]
    runner = {"Authorization": f"Bearer {seed_runner(hub, 'r1')}"}

    assert hub.client.get("/api/permits", headers=runner).status_code == 403
    assert hub.client.get(f"/api/permits/{permit_id}", headers=runner).status_code == 403
    assert hub.client.post(f"/api/permits/{permit_id}/grant", headers=runner).status_code == 403
