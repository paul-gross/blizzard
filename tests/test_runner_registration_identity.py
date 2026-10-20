"""A runner's identity at registration (component tier): the hub adds a runner under a minted id,
its token names that id at every registration, the name is whatever the runner last declared, and
the tenant is the one the runner was added in, under the name that tenant holds now.

Registrations go through a plain ``TestClient`` presenting the token ``add`` minted, as a runner
does with the token in its ``.env``."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from blizzard import __version__
from blizzard.hub.store import schema
from blizzard.hub.tenancy import TenantId
from blizzard.wire.version_window import VersionWindow, line_of
from tests.support import HubHarness, add_tenant, build_hub, default_project, default_tenant, seed_runner

pytestmark = pytest.mark.component


#: What a registration answers when the runner sent no bindings.
_NO_BINDINGS = {"bindings": None, "subscription": None, "subscription_unrostered": False}
_HANDSHAKE = {
    "hub_version": __version__,
    "window": {"floor": VersionWindow.load().floor, "ceiling": line_of(__version__)},
}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _tenant(hub: HubHarness, tenant: TenantId | None = None) -> dict[str, str]:
    """The tenant fields a registration answers for a runner of ``tenant`` (default: the store's
    only tenant), named as the store holds it now."""
    named = tenant if tenant is not None else default_tenant(hub.engine)
    with hub.engine.connect() as conn:
        name = conn.execute(sa.select(schema.tenants.c.name).where(schema.tenants.c.tenant_id == named)).scalar_one()
    return {"tenant_id": named, "tenant_name": name}


def _serving_default(hub: HubHarness, tenant: TenantId | None = None, *, entry: str | None = None) -> list[dict]:
    """The ``projects`` a registration answers for a runner serving ``tenant``'s only project (default: the store's
    only tenant's), declared as ``entry`` — the project's id, as adding a runner with no list stores it, when
    ``None``."""
    project_id = default_project(hub.engine, tenant)
    declared = project_id if entry is None else entry
    return [{"entry": declared, "project_id": project_id, "slug": "default", "name": "Default"}]


def _rename(hub: HubHarness, tenant: TenantId, name: str) -> None:
    """Rename ``tenant`` in the store and refresh the hub's open tenants, as renaming a tenant does."""
    with hub.engine.begin() as conn:
        conn.execute(sa.update(schema.tenants).where(schema.tenants.c.tenant_id == tenant).values(name=name))
    hub.hub_services.tenants.open_tenants.refresh()


def _register(hub: HubHarness, token: str, *, name: str | None = None, workspace_id: str = "w1") -> httpx.Response:
    assert hub.app is not None
    body: dict[str, object] = {"workspace_id": workspace_id}
    if name is not None:
        body["name"] = name
    return TestClient(hub.app).post("/api/fleet/runners", json=body, headers=_bearer(token))


def test_an_added_runner_first_registers_under_its_minted_id(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = hub.services.enrollment.add("r-claude", by="op")
    held = hub.services.registry.get_runner(added.runner_id)
    assert added.runner_id.startswith("rn_")
    assert held is not None and held.never_connected()
    assert (held.name, held.added_by) == ("r-claude", "op")

    first = _register(hub, added.token, name="r-claude")
    again = _register(hub, added.token, name="r-claude")

    assert first.status_code == 201, first.text
    assert first.json() == {
        "runner_id": added.runner_id,
        "projects": _serving_default(hub),
        **_NO_BINDINGS,
        **_HANDSHAKE,
        "runner_name": "r-claude",
        "first_registration": True,
        **_tenant(hub),
    }
    assert again.json()["first_registration"] is False
    registered = hub.services.registry.get_runner(added.runner_id)
    assert registered is not None and not registered.never_connected()


@pytest.mark.parametrize("declared", [None, "", "   "])
def test_a_registration_declaring_no_name_keeps_the_one_held(tmp_path: Path, declared: str | None) -> None:
    hub = build_hub(tmp_path)
    added = hub.services.enrollment.add("r-claude", by="op")

    resp = _register(hub, added.token, name=declared)

    assert resp.status_code == 201, resp.text
    assert resp.json()["runner_name"] == "r-claude"
    held = hub.services.registry.get_runner(added.runner_id)
    assert held is not None and held.name == "r-claude"


@pytest.mark.parametrize("paused", [False, True], ids=["live", "hub-paused"])
def test_a_restart_under_a_new_name_renames_the_runner_and_changes_nothing_else(tmp_path: Path, paused: bool) -> None:
    hub = build_hub(tmp_path)
    added = hub.services.enrollment.add("r-old", by="op")
    assert _register(hub, added.token, name="r-old").status_code == 201
    if paused:
        assert hub.client.post(f"/api/runners/{added.runner_id}/pause", json={"by": "op"}).status_code == 200
    before = hub.services.registry.get_runner(added.runner_id)

    renamed = _register(hub, added.token, name="r-new")

    assert renamed.json() == {
        "runner_id": added.runner_id,
        "projects": _serving_default(hub),
        **_NO_BINDINGS,
        **_HANDSHAKE,
        "runner_name": "r-new",
        "first_registration": False,
        **_tenant(hub),
    }
    after = hub.services.registry.get_runner(added.runner_id)
    assert before is not None and after is not None
    assert after.name == "r-new"
    assert (after.token_hash, after.hub_paused, after.registered_at, after.added_at) == (
        before.token_hash,
        paused,
        before.registered_at,
        before.added_at,
    )
    assert hub.client.get(f"/api/fleet/runners/{added.runner_id}", headers=_bearer(added.token)).status_code == 200


def test_two_runners_sharing_a_name_register_independently(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = hub.services.enrollment.add("r-claude", by="op")
    second = hub.services.enrollment.add("r-claude", by="op")
    assert first.runner_id != second.runner_id

    one = _register(hub, first.token, name="r-claude", workspace_id="w1")
    other = _register(hub, second.token, name="r-claude", workspace_id="w2")

    assert (one.json()["runner_id"], other.json()["runner_id"]) == (first.runner_id, second.runner_id)
    assert one.json()["first_registration"] is True and other.json()["first_registration"] is True
    workspaces = {
        runner_id: registration.workspace_id
        for runner_id in (first.runner_id, second.runner_id)
        if (registration := hub.services.registry.get_runner(runner_id)) is not None
    }
    assert workspaces == {first.runner_id: "w1", second.runner_id: "w2"}


def test_the_fleet_read_of_a_runner_that_never_registered_is_409(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = hub.services.enrollment.add("r-claude", by="op")

    resp = hub.client.get(f"/api/fleet/runners/{added.runner_id}", headers=_bearer(added.token))

    assert resp.status_code == 409
    assert resp.json() == {"detail": f"runner {added.runner_id} has not registered"}


def test_a_runner_added_in_another_tenant_registers_naming_that_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bravo = add_tenant(hub.engine, "bravo")
    token = seed_runner(hub.open_services(bravo), "rn-bravo-1", register=False)

    first = _register(hub, token, name="r-bravo")

    assert first.status_code == 201, first.text
    assert first.json() == {
        "runner_id": "rn-bravo-1",
        "projects": _serving_default(hub, bravo, entry="default"),
        **_NO_BINDINGS,
        **_HANDSHAKE,
        "runner_name": "r-bravo",
        "first_registration": True,
        "tenant_id": bravo,
        "tenant_name": "bravo",
    }


def test_a_renamed_tenant_reaches_its_runner_on_the_next_registration(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bravo = add_tenant(hub.engine, "bravo")
    token = seed_runner(hub.open_services(bravo), "rn-bravo-1")
    before = _register(hub, token)

    _rename(hub, bravo, "bravo-renamed")
    after = _register(hub, token)

    assert (before.json()["tenant_name"], after.json()["tenant_name"]) == ("bravo", "bravo-renamed")
    assert after.json()["tenant_id"] == bravo
