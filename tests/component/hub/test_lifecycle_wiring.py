"""The hub's process lifecycle owners as the container holds them: the hosted app boots over the process's one action
loop, step wake, pass driver, open tenants, readiness and flags, and its one cleanup closes the tenant scopes while the
loop still runs, stops the loop after, and leaves the borrowed engine open. A tenant's deletion through the container's
teardown evicts its scope, refreshes the open set and refuses its next request. The sweeps run from the container's
roster, timed and on request; the close drain acts in each tenant through that tenant's own drainer; and one poisoned
tenant fails alone in a hub-wide pass (component tier)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.flags import Flags, resolve_flags
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.foundation.registry.interface import ServiceAbsent
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.hub import app as hub_app
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.app import Sweep, create_app
from blizzard.hub.composition import open_hub_store
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.delivery.action_pass import ActionPassDriver
from blizzard.hub.delivery.flow_driver import StepWake
from blizzard.hub.domain.execution.apply import IHubStepWake
from blizzard.hub.domain.sweeps.names import SweepName
from blizzard.hub.domain.sweeps.roster import SweepRoster
from blizzard.hub.domain.tenancy.administration import TenantAdministration
from blizzard.hub.domain.tenancy.teardown import TenantTeardown
from blizzard.hub.domain.work_items.closure import CloseDrainSweep, CloseIntentDrainer
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.process_root import HubProcess
from blizzard.hub.scopes.tenants import TenantContainers
from blizzard.hub.tenancy import StoreScope, TenantClosed, TenantId, UnknownTenant
from blizzard.wire.tenant import TENANT_NOT_FOUND
from tests.support.config_inputs import FLAG_REGISTRY, build_hosted_hub_app, init_hub
from tests.support.scaffolding import HubHarness, build_hub, default_tenant
from tests.support.tenant_sweeps import close_events, landed_hub_ref

# The owners hold what they were built over privately; reading it is the only way to see it is shared.
# pyright: reportPrivateUsage=false


def test_the_hosted_app_boots_over_the_containers_lifecycle_owners(tmp_path: Path) -> None:
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    process: HubProcess = app.state.hub_process
    resolver = process.resolver
    loop = resolver.get(ActionLoop)

    assert app.state.readiness is resolver.get(ReadinessService)
    assert app.state.flags is resolver.get(Flags)
    assert resolver.get(IHubStepWake) is resolver.get(StepWake)
    assert resolver.get(ActionPassDriver)._loop is loop
    with TestClient(app) as client:
        assert loop._loop is not None
        assert client.get("/api/ready").json()["ready"] is True
    assert loop._loop is None


def test_the_hosted_cleanup_closes_the_tenant_scopes_before_the_loop_and_leaves_the_engine_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = init_hub(tmp_path / "hub")
    engine, connections = open_hub_store(config.db_url, DisabledPlatformTracing())
    events: list[str] = []
    original_close = TenantContainers.close
    original_stop = ActionLoop.stop

    def close_tenants(tenants: TenantContainers) -> None:
        events.append("tenants")
        original_close(tenants)

    def stop(loop: ActionLoop) -> None:
        events.append("loop" if loop._loop is not None else "loop-idle")
        original_stop(loop)

    dispose = engine.dispose

    def record_dispose(close: bool = True) -> None:
        events.append("dispose")
        dispose(close)

    monkeypatch.setattr(TenantContainers, "close", close_tenants)
    monkeypatch.setattr(ActionLoop, "stop", stop)
    monkeypatch.setattr(engine, "dispose", record_dispose)
    try:
        app = build_hosted_hub_app(config, engine=engine, connections=connections)
        app.state.hub_process.resolver.get(ActionLoop).start()
        app.state.hosted_cleanup.close()

        assert events == ["tenants", "loop"]
    finally:
        dispose()


def test_a_failed_boot_still_stops_the_loop_it_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[ActionLoop] = []
    stopped: list[ActionLoop] = []
    original_init = ActionLoop.__init__
    original_stop = ActionLoop.stop

    def init(loop: ActionLoop, *args: Any, **kwargs: Any) -> None:
        original_init(loop, *args, **kwargs)
        built.append(loop)

    def stop(loop: ActionLoop) -> None:
        stopped.append(loop)
        original_stop(loop)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boot failed")

    monkeypatch.setattr(ActionLoop, "__init__", init)
    monkeypatch.setattr(ActionLoop, "stop", stop)
    monkeypatch.setattr(hub_app, "build_flow_driver", fail)
    with pytest.raises(RuntimeError, match="boot failed"):
        build_hosted_hub_app(init_hub(tmp_path / "hub"))
    # Counted per loop: another test's process may stop its own loop while this one runs.
    assert len(built) == 1
    assert stopped.count(built[0]) == 1


def test_the_store_free_app_declares_every_lifecycle_owner_absent(tmp_path: Path) -> None:
    app = create_app(HubConfig(root=tmp_path, db_url="sqlite://"), flags=resolve_flags(FLAG_REGISTRY, "hub", {}).flags)
    resolver = app.state.hub_process.resolver
    for missing in (ActionLoop, ActionPassDriver, StepWake, IHubStepWake, OpenTenants, ReadinessService):
        with pytest.raises(ServiceAbsent):
            resolver.get(missing)
    assert isinstance(resolver.get(Flags), Flags)
    with TestClient(app):
        pass


def test_a_tenant_deletion_evicts_its_scope_refreshes_the_open_set_and_refuses_its_next_request(tmp_path: Path) -> None:
    app = build_hub(tmp_path).wired_app
    process: HubProcess = app.state.hub_process
    try:
        resolver = process.resolver
        tenant = resolver.get(TenantAdministration).create("acme", admin_user_id=None, by="usr_creator").tenant_id
        opened = process.tenants.open(StoreScope(tenant))
        open_tenants = resolver.get(OpenTenants)
        assert tenant in open_tenants.every()

        resolver.get(TenantTeardown).delete(tenant, by="usr_deleter")

        with pytest.raises(TenantClosed):
            opened.resolver.get(StoreScope)
        with pytest.raises((TenantClosed, UnknownTenant)):
            process.tenants.open(StoreScope(tenant))
        assert tenant not in open_tenants.every()
        response = TestClient(app, raise_server_exceptions=False).get("/api/chunks", headers={TENANT_HEADER: tenant})
        assert (response.status_code, response.json()["type"]) == (404, TENANT_NOT_FOUND)
    finally:
        process.close()


def _two_tenants_landed(tmp_path: Path) -> tuple[HubHarness, TenantId, TenantId]:
    """A hub whose default tenant and a second one each hold a landed chunk with a pending close intent."""
    hub = build_hub(tmp_path, public_url="https://hub.example")
    carried = default_tenant(hub.engine)
    other = hub.add_tenant("other").tenant
    for tenant, chunk_id in ((carried, "ch_carried"), (other, "ch_other")):
        landed_hub_ref(hub, tenant, chunk_id)
    return hub, carried, other


def _pending(process: HubProcess, tenant: TenantId) -> list[str]:
    drainer = process.tenants.resolve(StoreScope(tenant), CloseIntentDrainer)
    return [intent.chunk_id for intent in drainer._delivery.pending_close_intents()]


def test_a_timed_pass_runs_the_containers_roster(tmp_path: Path) -> None:
    hub, carried, other = _two_tenants_landed(tmp_path)
    app = hub.wired_app
    roster = app.state.hub_process.resolver.get(SweepRoster)

    timed = {sweep.config_name: sweep for sweep in Sweep.all(app)}
    timed[SweepName.CLOSE_DRAIN].reconciler()

    assert set(timed) <= set(roster.names())
    assert close_events(hub, carried) == ["ch_carried"]
    assert close_events(hub, other) == ["ch_other"]


def test_an_on_request_pass_runs_the_containers_roster(tmp_path: Path) -> None:
    hub, carried, other = _two_tenants_landed(tmp_path)

    response = hub.client.post("/api/admin/sweeps/close_drain/passes", headers={TENANT_HEADER: carried})

    assert (response.status_code, response.json()["outcome"]) == (200, "completed")
    assert close_events(hub, carried) == ["ch_carried"]
    assert close_events(hub, other) == ["ch_other"]


def test_the_close_drain_acts_in_each_tenant_through_its_own_drainer(tmp_path: Path) -> None:
    hub, carried, other = _two_tenants_landed(tmp_path)
    process: HubProcess = hub.wired_app.state.hub_process
    drain = process.resolver.get(CloseDrainSweep)

    assert process.resolver.get(SweepRoster)._every_tenant[SweepName.CLOSE_DRAIN] is drain
    for tenant in (carried, other):
        drainer = process.tenants.resolve(StoreScope(tenant), CloseIntentDrainer)
        assert drain._tenant(tenant) is drainer
        assert drainer._public_url == "https://hub.example"
    drain.sweep()

    assert (_pending(process, carried), _pending(process, other)) == ([], [])


def test_one_poisoned_tenant_fails_alone_in_a_hub_wide_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, carried, other = _two_tenants_landed(tmp_path)
    process: HubProcess = hub.wired_app.state.hub_process
    poisoned = process.tenants.resolve(StoreScope(other), CloseIntentDrainer)

    def refused(*_: object) -> None:
        raise RuntimeError("the forge refused the close")

    # The tenant scope's own instance, so only that tenant's half raises.
    monkeypatch.setattr(poisoned, "drain", refused)
    result = process.resolver.get(SweepRoster).pass_every_tenant(SweepName.CLOSE_DRAIN)

    assert (result.outcome.value, result.error) == ("completed", None)
    assert _pending(process, carried) == []
    assert _pending(process, other) == ["ch_other"]
