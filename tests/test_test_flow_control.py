"""The hosted flow-step route is fenced, authorized, and serialized on the controlled clock's lock."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.foundation.clock import ControlledClock
from blizzard.hub.api.test_flow_control import flow_control_router
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.delivery.flow_driver import FlowDriver, TickResult
from tests.support.config_inputs import build_hosted_hub_app, create_hub_app, init_hub
from tests.support.scaffolding import build_hub, runner_token, seed_hub_admin, seed_runner, seed_session, seed_user

pytestmark = pytest.mark.component


@pytest.mark.parametrize("fence", [None, "true", "0"])
def test_step_route_requires_the_clock_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fence: str | None
) -> None:
    monkeypatch.setenv("BLIZZARD_CLOCK", "controlled")
    if fence is None:
        monkeypatch.delenv("BLIZZARD_CLOCK_FENCE", raising=False)
    else:
        monkeypatch.setenv("BLIZZARD_CLOCK_FENCE", fence)
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    try:
        assert app.state.flow_driver is not None
        with TestClient(app) as client:
            assert client.post("/api/admin/test-flow/step").status_code in (404, 405)
    finally:
        app.state.engine.dispose()


def test_step_route_requires_hub_admin_and_rejects_runners(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    clock = ControlledClock(datetime(2025, 1, 1, tzinfo=UTC))
    app = create_hub_app(
        hub.wired_app.state.config,
        services=hub.hub_services,
        test_flow_router=flow_control_router(hub.flow_driver, clock, asyncio.Lock()),
    )
    tenant = seed_session(hub, seed_user(hub, username="tenant-admin", role=Role.ADMIN))
    user = seed_user(hub, username="hub-admin", role=Role.PENDING)
    seed_hub_admin(hub.engine, user.user_id)
    admin = seed_session(hub, user)
    seed_runner(hub, "r1")
    url = "/api/admin/test-flow/step"
    with TestClient(app) as client:
        assert client.post(url).status_code == 401
        assert client.post(url, headers={"Authorization": f"Bearer {tenant}"}).status_code == 403
        assert client.post(url, headers={"Authorization": f"Bearer {runner_token('r1')}"}).status_code == 403
        assert client.post(url, headers={"Authorization": f"Bearer {admin}"}).status_code == 200


@pytest.mark.asyncio
async def test_step_waits_on_a_held_lock_and_answers_after_the_move() -> None:
    clock = ControlledClock(datetime(2025, 1, 1, tzinfo=UTC))
    lock = asyncio.Lock()
    initial = clock.now()
    driver = Mock(spec=FlowDriver)
    driver.step = AsyncMock(return_value=TickResult())
    step = next(r.endpoint for r in flow_control_router(driver, clock, lock).routes if isinstance(r, APIRoute))
    async with lock:
        stepping = asyncio.create_task(step())
        await asyncio.sleep(0)
        assert not stepping.done()
        driver.step.assert_not_called()
        clock.advance(timedelta(seconds=10))
    answer = await asyncio.wait_for(stepping, timeout=5)
    assert answer["now"] == (initial + timedelta(seconds=10)).isoformat()
