"""A runner's identity at registration (component tier): the hub adds a runner under a minted id,
its token names that id at every registration, and the name is whatever the runner last declared.

Registrations go through a plain ``TestClient`` presenting the token ``add`` minted, as a runner
does with the token in its ``.env``."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
    assert first.json() == {"runner_id": added.runner_id, "runner_name": "r-claude", "first_registration": True}
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

    assert renamed.json() == {"runner_id": added.runner_id, "runner_name": "r-new", "first_registration": False}
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
