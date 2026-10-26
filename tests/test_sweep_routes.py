"""Sweeps on request over the hub app: ``POST /api/sweeps/{name}/passes`` runs one pass over the request's tenant
alone and ``POST /api/admin/sweeps/{name}/passes`` one over every tenant, each answering once the pass has finished,
in either mode; the refusals, and who may call which."""

from __future__ import annotations

import dataclasses
import threading
from datetime import datetime
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.sweeps.names import SweepMode, SweepModes, SweepName
from blizzard.hub.domain.work_items.closure import CloseIntentDrainer
from blizzard.hub.tenancy import TenantId
from tests.support import (
    HubHarness,
    build_hub,
    default_tenant,
    runner_token,
    seed_hub_admin,
    seed_runner,
    seed_session,
    seed_user,
)
from tests.test_sweeps_per_tenant import _close_events, _landed_hub_ref

pytestmark = pytest.mark.component

_TENANT_PASS = "/api/sweeps/{}/passes"
_HUB_PASS = "/api/admin/sweeps/{}/passes"


@dataclasses.dataclass
class _Hub:
    hub: HubHarness
    carried: TenantId
    other: TenantId
    stores: dict[TenantId, ChunkStores]

    def post(self, path: str, tenant: TenantId | None = None) -> dict[str, object]:
        response = self.hub.client.post(path, headers={TENANT_HEADER: tenant or self.carried})
        assert response.status_code == 200, response.text
        return response.json()

    def pending(self, tenant: TenantId) -> list[str]:
        return [intent.chunk_id for intent in self.stores[tenant].delivery.pending_close_intents()]


def _two_tenants(tmp_path: Path, mode: SweepMode = SweepMode.TIMED) -> _Hub:
    hub = build_hub(tmp_path)
    carried = default_tenant(hub.engine)
    other = hub.add_tenant("other").tenant
    stores = {carried: _landed_hub_ref(hub, carried, "ch_carried"), other: _landed_hub_ref(hub, other, "ch_other")}
    app = hub.wired_app
    app.state.config = dataclasses.replace(app.state.config, sweeps=SweepModes(dict.fromkeys(SweepName, mode)))
    return _Hub(hub, carried, other, stores)


def _completed_in_order(body: dict[str, object]) -> None:
    assert (body["outcome"], body["error"]) == ("completed", None)
    assert datetime.fromisoformat(str(body["started_at"])) <= datetime.fromisoformat(str(body["finished_at"]))


@pytest.mark.parametrize("mode", list(SweepMode))
def test_a_tenant_pass_answers_once_its_writes_are_readable_and_leaves_the_other_tenant_untouched(
    tmp_path: Path, mode: SweepMode
) -> None:
    world = _two_tenants(tmp_path, mode)

    _completed_in_order(world.post(_TENANT_PASS.format("close_drain")))

    assert world.pending(world.carried) == []
    assert _close_events(world.hub, world.carried) == ["ch_carried"]
    assert world.pending(world.other) == ["ch_other"]
    assert _close_events(world.hub, world.other) == []


@pytest.mark.parametrize("mode", list(SweepMode))
def test_a_pass_over_every_tenant_answers_once_every_tenants_writes_are_readable(
    tmp_path: Path, mode: SweepMode
) -> None:
    world = _two_tenants(tmp_path, mode)

    _completed_in_order(world.post(_HUB_PASS.format("close_drain")))

    for tenant, chunk_id in ((world.carried, "ch_carried"), (world.other, "ch_other")):
        assert world.pending(tenant) == []
        assert _close_events(world.hub, tenant) == [chunk_id]


@pytest.mark.parametrize("name", ["forge_status", "event_derivation"])
def test_each_sweep_a_tenant_pass_serves_completes(tmp_path: Path, name: str) -> None:
    world = _two_tenants(tmp_path)

    _completed_in_order(world.post(_TENANT_PASS.format(name)))


@pytest.mark.parametrize("name", ["forge_status", "event_derivation", "tenant_teardown"])
def test_each_built_sweep_runs_over_every_tenant(tmp_path: Path, name: str) -> None:
    world = _two_tenants(tmp_path)

    _completed_in_order(world.post(_HUB_PASS.format(name)))


@pytest.mark.parametrize("name", ["nope", "trace_export", "egress"])
@pytest.mark.parametrize("route", [_TENANT_PASS, _HUB_PASS])
def test_a_name_no_built_sweep_goes_by_answers_404(tmp_path: Path, route: str, name: str) -> None:
    world = _two_tenants(tmp_path)

    response = world.hub.client.post(route.format(name), headers={TENANT_HEADER: world.carried})

    assert response.status_code == 404


def test_a_tenant_pass_of_a_sweep_over_every_tenant_only_answers_422(tmp_path: Path) -> None:
    world = _two_tenants(tmp_path)

    response = world.hub.client.post(_TENANT_PASS.format("tenant_teardown"), headers={TENANT_HEADER: world.carried})

    assert response.status_code == 422


def test_a_pass_whose_lock_is_held_answers_409_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _two_tenants(tmp_path)
    drain = world.hub.hub_services.close_drain
    entered, release = threading.Event(), threading.Event()
    hub_wide = drain.sweep

    def held() -> None:
        entered.set()
        release.wait(timeout=10)
        hub_wide()

    monkeypatch.setattr(drain, "sweep", held)
    running = threading.Thread(target=lambda: world.post(_HUB_PASS.format("close_drain")))
    running.start()
    assert entered.wait(timeout=10)

    try:
        refusals = [
            world.hub.client.post(route.format("close_drain"), headers={TENANT_HEADER: world.carried})
            for route in (_TENANT_PASS, _HUB_PASS)
        ]
    finally:
        release.set()
        running.join(timeout=10)

    assert [(r.status_code, r.json()) for r in refusals] == [(409, {"code": "sweep_in_progress"})] * 2
    assert world.pending(world.carried) == []


def test_a_tenant_pass_whose_tenant_raises_answers_failed_with_its_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _two_tenants(tmp_path)

    def refused(self: CloseIntentDrainer, *_: object) -> None:
        raise RuntimeError("the forge refused the close")

    monkeypatch.setattr(CloseIntentDrainer, "drain", refused)

    body = world.post(_TENANT_PASS.format("close_drain"))

    assert (body["outcome"], body["error"]) == ("failed", "the forge refused the close")
    assert world.pending(world.carried) == ["ch_carried"]


# --- who may call which ----------------------------------------------------------------------


@pytest.fixture
def signed_in(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    default_tenant(hub.engine)
    return hub


def _as(hub: HubHarness, token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", TENANT_HEADER: default_tenant(hub.engine)}


def test_a_tenant_admin_runs_a_tenant_pass_and_a_hub_administrator_one_over_every_tenant(
    signed_in: HubHarness,
) -> None:
    admin = seed_session(signed_in, seed_user(signed_in, username="tia", role=Role.ADMIN))
    hal = seed_user(signed_in, username="hal", role=Role.PENDING)
    seed_hub_admin(signed_in.engine, hal.user_id)

    tenant_pass = signed_in.client.post(_TENANT_PASS.format("close_drain"), headers=_as(signed_in, admin))
    hub_pass = signed_in.client.post(
        _HUB_PASS.format("close_drain"), headers={"Authorization": f"Bearer {seed_session(signed_in, hal)}"}
    )

    assert (tenant_pass.status_code, hub_pass.status_code) == (200, 200)


def test_a_member_below_admin_is_refused_a_tenant_pass(signed_in: HubHarness) -> None:
    member = seed_session(signed_in, seed_user(signed_in, username="cora", role=Role.CONTRIBUTOR))

    response = signed_in.client.post(_TENANT_PASS.format("close_drain"), headers=_as(signed_in, member))

    assert response.status_code == 403


def test_a_tenant_admin_who_is_no_hub_administrator_is_refused_a_pass_over_every_tenant(
    signed_in: HubHarness,
) -> None:
    admin = seed_session(signed_in, seed_user(signed_in, username="tia", role=Role.ADMIN))

    response = signed_in.client.post(_HUB_PASS.format("close_drain"), headers=_as(signed_in, admin))

    assert response.status_code == 403


@pytest.mark.parametrize("route", [_TENANT_PASS, _HUB_PASS])
def test_a_runner_principal_is_refused_either_pass(signed_in: HubHarness, route: str) -> None:
    seed_runner(signed_in, "r1")

    response = signed_in.client.post(route.format("close_drain"), headers=_as(signed_in, runner_token("r1")))

    assert response.status_code == 403
