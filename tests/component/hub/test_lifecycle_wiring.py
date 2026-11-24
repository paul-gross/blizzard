"""The hub's process lifecycle owners as the container holds them: the hosted app boots over the process's one action
loop, step wake, pass driver, open tenants, readiness and flags, and its one cleanup closes the tenant scopes while the
loop still runs, stops the loop after, and leaves the borrowed engine open (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from blizzard.foundation.flags import Flags, resolve_flags
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.foundation.registry.interface import ServiceAbsent
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.hub import app as hub_app
from blizzard.hub.app import create_app
from blizzard.hub.composition import open_hub_store
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.delivery.action_pass import ActionPassDriver
from blizzard.hub.delivery.flow_driver import StepWake
from blizzard.hub.domain.execution.apply import IHubStepWake
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.process_root import HubProcess
from blizzard.hub.scopes.tenants import TenantContainers
from tests.support.config_inputs import FLAG_REGISTRY, build_hosted_hub_app, init_hub

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
    assert app.state.services.tenants.open_tenants is resolver.get(OpenTenants)
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

    monkeypatch.setattr(TenantContainers, "close", close_tenants)
    monkeypatch.setattr(ActionLoop, "stop", stop)
    try:
        app = build_hosted_hub_app(config, engine=engine, connections=connections)
        app.state.hub_process.resolver.get(ActionLoop).start()
        app.state.hosted_cleanup.close()

        assert events == ["tenants", "loop"]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()


def test_a_failed_boot_still_stops_the_loop_it_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[bool] = []
    original_stop = ActionLoop.stop

    def stop(loop: ActionLoop) -> None:
        stopped.append(True)
        original_stop(loop)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boot failed")

    monkeypatch.setattr(ActionLoop, "stop", stop)
    monkeypatch.setattr(hub_app, "build_flow_driver", fail)
    with pytest.raises(RuntimeError, match="boot failed"):
        build_hosted_hub_app(init_hub(tmp_path / "hub"))
    assert stopped == [True]


def test_the_store_free_app_declares_every_lifecycle_owner_absent(tmp_path: Path) -> None:
    app = create_app(HubConfig(root=tmp_path, db_url="sqlite://"), flags=resolve_flags(FLAG_REGISTRY, "hub", {}).flags)
    resolver = app.state.hub_process.resolver
    for missing in (ActionLoop, ActionPassDriver, StepWake, IHubStepWake, OpenTenants, ReadinessService):
        with pytest.raises(ServiceAbsent):
            resolver.get(missing)
    assert isinstance(resolver.get(Flags), Flags)
    with TestClient(app):
        pass
