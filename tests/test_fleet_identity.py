"""``GET /api/fleet/identity`` — whom a runner's bearer token names, or why the hub refuses it
(component tier).

Every refusal is reached through the real verbs and read through a plain ``TestClient``, which
presents only the token a test hands it. The route answers 200 or a typed 401 and writes nothing."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from blizzard.foundation.flags import FlagRegistry, resolve_flags
from blizzard.hub.secrets import ENV_SECRET_KEY
from blizzard.hub.store import schema as s
from blizzard.hub.tenancy import TenantId
from tests.config_inputs import build_hosted_hub_app, init_hub, load_hub_config
from tests.support import HubHarness, add_tenant, build_hub, default_tenant, hub_scoped, seed_runner

pytestmark = pytest.mark.component


def _plain(hub: HubHarness) -> TestClient:
    assert hub.app is not None
    return TestClient(hub.app)


def _identity(hub: HubHarness, token: str | None) -> tuple[int, dict[str, object]]:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else None
    resp = _plain(hub).get("/api/fleet/identity", headers=headers)
    return resp.status_code, resp.json()


def _tenant(hub: HubHarness, tenant: TenantId | None = None) -> dict[str, str]:
    """The tenant fields the route answers for a runner of ``tenant`` (default: the store's only
    tenant), named as the store holds it now."""
    named = tenant if tenant is not None else default_tenant(hub.engine)
    with hub.engine.connect() as conn:
        name = conn.execute(sa.select(s.tenants.c.name).where(s.tenants.c.tenant_id == named)).scalar_one()
    return {"tenant_id": named, "tenant_name": name}


def _event_rows(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return int(conn.execute(hub_scoped(sa.select(sa.func.count()).select_from(s.event_log))).scalar_one())


_FLEET_REGISTRY = """
[flag.fleet-thing]
owner = "epic:ui-toolkit"
summary = "A capability hub and runners agree on"
scope = "fleet"
default = "off"

[flag.hub-thing]
owner = "epic:x"
summary = "A hub capability"
scope = "hub"
default = "on"

[flag.runner-thing]
owner = "epic:x"
summary = "A runner capability"
scope = "runner"
default = "on"

[retired]
old-fleet = "fleet"
old-hub = "hub"
"""


def test_the_answer_carries_the_hubs_fleet_flags_and_nothing_of_another_scope(tmp_path: Path) -> None:
    registry = FlagRegistry.parse(_FLEET_REGISTRY, "fixture")
    hub = build_hub(tmp_path, flags=resolve_flags(registry, "hub", {"fleet-thing": True}).flags)
    added = hub.services.enrollment.add("r-claude", by="op")

    status, body = _identity(hub, added.token)

    assert status == 200
    assert body["fleet_flags"] == {"fleet-thing": True, "old-fleet": True}


def test_a_hub_over_the_empty_packaged_registry_answers_no_fleet_flags(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = hub.services.enrollment.add("r-claude", by="op")

    assert _identity(hub, added.token)[1]["fleet_flags"] == {}


def test_a_token_names_its_added_runner_before_it_ever_registers(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = hub.services.enrollment.add("r-claude", by="op")

    assert _identity(hub, added.token) == (
        200,
        {"runner_id": added.runner_id, "runner_name": "r-claude", **_tenant(hub), "fleet_flags": {}},
    )


def test_a_registered_runners_token_names_it_with_its_latest_name(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "runner-a", name="r-old")
    registered = _plain(hub).post(
        "/api/fleet/runners",
        json={"name": "r-new", "workspace_id": "w1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert registered.status_code == 201, registered.text

    assert _identity(hub, token) == (
        200,
        {"runner_id": "runner-a", "runner_name": "r-new", **_tenant(hub), "fleet_flags": {}},
    )


def test_a_token_names_the_tenant_its_runner_was_added_in(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bravo = add_tenant(hub.engine, "bravo")
    token = seed_runner(hub.open_services(bravo), "rn-bravo-1", name="r-bravo", register=False)
    home = seed_runner(hub, "runner-a")

    assert _identity(hub, token) == (
        200,
        {
            "runner_id": "rn-bravo-1",
            "runner_name": "r-bravo",
            "tenant_id": bravo,
            "tenant_name": "bravo",
            "fleet_flags": {},
        },
    )
    assert _identity(hub, home)[1]["tenant_id"] == default_tenant(hub.engine)


def test_no_token_is_refused_as_missing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert _identity(hub, None) == (401, {"reason": "missing", "runner_id": None})


def test_a_token_the_hub_never_issued_is_refused_as_unknown(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, "runner-a")
    assert _identity(hub, "never-issued") == (401, {"reason": "unknown", "runner_id": None})


def test_a_token_rotated_away_is_refused_as_revoked_naming_its_runner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    old = seed_runner(hub, "runner-a")
    assert hub.client.post("/api/runners/runner-a/enrollments").status_code == 201

    assert _identity(hub, old) == (401, {"reason": "revoked", "runner_id": "runner-a"})


def test_a_retired_runners_token_is_refused_as_retired_naming_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "runner-a")
    assert hub.client.post("/api/runners/runner-a/retire", json={"by": "op"}).status_code == 200

    assert _identity(hub, token) == (401, {"reason": "retired", "runner_id": "runner-a"})


def test_a_reinstated_runners_old_token_is_refused_as_revoked(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "runner-a")
    assert hub.client.post("/api/runners/runner-a/retire", json={"by": "op"}).status_code == 200
    assert hub.client.post("/api/runners/runner-a/reinstate", json={"by": "op"}).status_code == 200

    assert _identity(hub, token) == (401, {"reason": "revoked", "runner_id": "runner-a"})


def test_answering_registers_nothing_and_records_no_liveness(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    connected = seed_runner(hub, "runner-a")
    never_connected = seed_runner(hub, "runner-b", register=False)
    before = hub.services.registry.get_runner("runner-a")
    frames, events = hub.services.events.latest_id(), _event_rows(hub)
    hub.clock.advance(timedelta(seconds=60))

    for token in (connected, never_connected, "never-issued", None):
        _identity(hub, token)

    assert hub.services.registry.get_runner("runner-a") == before
    after_b = hub.services.registry.get_runner("runner-b")
    assert after_b is not None and after_b.never_connected()
    assert hub.services.events.latest_id() == frames
    assert _event_rows(hub) == events


@pytest.mark.parametrize("value", ["enforce", "warn", "block"])
def test_a_hub_whose_toml_still_sets_runner_auth_mode_boots_and_refuses_a_tokenless_fleet_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    config = init_hub(tmp_path / "hub")
    # Top-level, ahead of the generated tables, where a deployed hub toml carries it.
    config.config_path.write_text(f'runner_auth_mode = "{value}"\n' + config.config_path.read_text())

    app = build_hosted_hub_app(load_hub_config(config.root))
    try:
        client = TestClient(app)
        assert client.post("/api/fleet/runners", json={"workspace_id": "w1"}).status_code == 401
        assert client.get("/api/fleet/identity").json() == {"reason": "missing", "runner_id": None}
    finally:
        app.state.engine.dispose()
