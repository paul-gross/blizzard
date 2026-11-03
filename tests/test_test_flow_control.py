"""Hosted test controls are fenced, authorized and share the driver's clock."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.foundation.clock import FixedClock
from blizzard.hub.api.test_flow_control import AdvanceClock, flow_control_router
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.delivery.flow_driver import FlowDriver, TickResult
from tests.config_inputs import build_hosted_hub_app, create_hub_app, init_hub
from tests.support import build_hub, runner_token, seed_hub_admin, seed_runner, seed_session, seed_user

pytestmark = pytest.mark.component


@pytest.mark.parametrize("step,fence", [(None, None), ("1", None), (None, "1"), ("true", "1")])
def test_controls_require_both_fences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str | None, fence: str | None
) -> None:
    for name, value in (("BLIZZARD_TEST_FLOW_STEP", step), ("BLIZZARD_CRASH_FENCE", fence)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    assert app.state.flow_driver is not None
    with TestClient(app) as client:
        assert client.post("/api/admin/test-flow/step").status_code in (404, 405)
    app.state.engine.dispose()


@pytest.mark.asyncio
async def test_advance_waits_for_the_whole_step() -> None:
    clock = FixedClock(datetime(2025, 1, 1, tzinfo=UTC))
    initial = clock.now()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held_step() -> TickResult:
        entered.set()
        await release.wait()
        assert clock.now() == initial
        return TickResult()

    driver = Mock(spec=FlowDriver)
    driver.step = AsyncMock(side_effect=held_step)
    router = flow_control_router(driver, clock)
    step = next(
        route.endpoint for route in router.routes if isinstance(route, APIRoute) and route.path.endswith("/step")
    )
    advance = next(
        route.endpoint for route in router.routes if isinstance(route, APIRoute) and route.path.endswith("/advance")
    )
    stepping = asyncio.create_task(step())
    await asyncio.wait_for(entered.wait(), timeout=5)
    advancing = asyncio.create_task(advance(AdvanceClock(seconds=10)))
    try:
        await asyncio.sleep(0)
        assert not advancing.done()
        assert clock.now() == initial
    finally:
        release.set()
    assert (await stepping)["now"] == initial.isoformat()
    assert (await advancing)["now"] == (initial + timedelta(seconds=10)).isoformat()


def test_controls_require_hub_admin_and_reject_runners(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    app = create_hub_app(
        hub.wired_app.state.config,
        services=hub.hub_services,
        test_flow_router=flow_control_router(hub.flow_driver, hub.clock),
    )
    admin = seed_session(hub, seed_user(hub, username="tenant-admin", role=Role.ADMIN))
    user = seed_user(hub, username="hub-admin", role=Role.PENDING)
    seed_hub_admin(hub.engine, user.user_id)
    token = seed_session(hub, user)
    seed_runner(hub, "r1")
    with TestClient(app) as client:
        for path, body in (("step", None), ("advance", {"seconds": 1})):
            url = f"/api/admin/test-flow/{path}"
            assert client.post(url, json=body).status_code == 401
            assert client.post(url, json=body, headers={"Authorization": f"Bearer {admin}"}).status_code == 403
            assert (
                client.post(url, json=body, headers={"Authorization": f"Bearer {runner_token('r1')}"}).status_code
                == 403
            )
            assert client.post(url, json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_controls_step_and_advance_the_hosted_shared_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLIZZARD_TEST_FLOW_STEP", "1")
    monkeypatch.setenv("BLIZZARD_CRASH_FENCE", "1")
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    assert app.state.flow_driver is None
    clock = app.state.services.clock
    assert isinstance(clock, FixedClock)
    with TestClient(app) as client:
        first = client.post("/api/admin/test-flow/step")
        assert first.status_code == 200, first.text
        initial = datetime.fromisoformat(first.json()["now"])
        assert (first.json()["reconciled"], first.json()["skipped"], first.json()["failed"]) == (1, 0, 0)
        second = client.post("/api/admin/test-flow/step")
        assert (second.json()["reconciled"], second.json()["skipped"], second.json()["failed"]) == (0, 1, 0)
        moved = client.post("/api/admin/test-flow/advance", json={"seconds": 120})
        assert datetime.fromisoformat(moved.json()["now"]) == initial + timedelta(seconds=120)
        assert clock.now() == initial + timedelta(seconds=120)
        assert client.post("/api/admin/test-flow/advance", json={"seconds": -1}).status_code == 422
        assert client.post("/api/admin/test-flow/step").json()["reconciled"] == 1
    app.state.engine.dispose()
