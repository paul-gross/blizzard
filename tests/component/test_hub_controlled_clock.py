"""The fenced clock and flow step share one serialized controlled timeline."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.foundation.clock import ControlledClock
from blizzard.foundation.web import Frontend
from blizzard.hub.api.clock import clock_router
from blizzard.hub.api.test_flow_control import controlled_flow_router
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.hub.delivery.flow_driver import FlowDriver, TickResult
from blizzard.hub.domain.delivery.flows import RECONCILE_FLOOR
from blizzard.hub.runtime import migration_runner
from blizzard.wire.clock import ClockMoveRequest
from tests.config_inputs import build_hosted_hub_app, create_hub_app, init_hub
from tests.support import build_hub, runner_token, seed_hub_admin, seed_runner, seed_session, seed_user


@pytest.mark.asyncio
async def test_cancelled_controlled_step_drains_a_failing_pass_before_releasing_lock() -> None:
    clock = ControlledClock(datetime(2025, 1, 1, tzinfo=UTC))
    lock = asyncio.Lock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def failing_step() -> TickResult:
        entered.set()
        await release.wait()
        raise RuntimeError("probe down")

    driver = Mock(spec=FlowDriver)
    driver.step = AsyncMock(side_effect=failing_step)
    step = next(r.endpoint for r in controlled_flow_router(driver, clock, lock).routes if isinstance(r, APIRoute))
    stepping = asyncio.create_task(step())
    await asyncio.wait_for(entered.wait(), timeout=5)
    stepping.cancel()
    await asyncio.sleep(0)
    assert lock.locked() and not stepping.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await stepping
    assert not lock.locked()


def test_clock_routes_on_store_behind_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLIZZARD_CLOCK", "controlled")
    monkeypatch.setenv("BLIZZARD_CLOCK_FENCE", "1")
    monkeypatch.delenv("BLIZZARD_TEST_FLOW_STEP", raising=False)
    config = init_hub(tmp_path / "hub")
    migration_runner(config).downgrade("base")
    app = build_hosted_hub_app(config)
    try:
        with TestClient(app) as client:
            assert client.get("/api/ready").json()["ready"] is False
            assert client.get("/api/admin/clock").status_code == 200
            assert client.post("/api/admin/clock", json={"advance_seconds": 1}).status_code == 200
            assert not any(isinstance(r, APIRoute) and r.path == "/api/admin/test-flow/step" for r in app.routes)
    finally:
        app.state.engine.dispose()


def test_clock_and_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLIZZARD_CLOCK", "controlled")
    monkeypatch.setenv("BLIZZARD_CLOCK_FENCE", "1")
    monkeypatch.delenv("BLIZZARD_TEST_FLOW_STEP", raising=False)
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    try:
        with TestClient(app) as client:
            initial = client.get("/api/admin/clock").json()["now"]
            assert client.post("/api/admin/test-flow/step").json()["reconciled"] == 1
            assert client.post("/api/admin/test-flow/step").json()["skipped"] == 1
            events = client.get("/api/admin/events?limit=100").json()
            moved = client.post("/api/admin/clock", json={"advance_seconds": 90.5})
            assert moved.json() == {
                "previous": initial,
                "now": (datetime.fromisoformat(initial) + timedelta(seconds=90.5)).isoformat(),
            }
            now = moved.json()["now"]
            assert client.post("/api/admin/clock", json={"to": now}).json() == {"previous": now, "now": now}
            assert client.post("/api/admin/clock", json={"advance_seconds": 0}).json()["now"] == now
            backwards = client.post("/api/admin/clock", json={"to": initial})
            assert backwards.status_code == 409
            assert backwards.json() == {"code": "clock_backwards"}
            for body in (
                {},
                {"to": now, "advance_seconds": 1},
                {"advance_seconds": -1},
                {"advance_seconds": "NaN"},
                {"advance_seconds": "Infinity"},
                {"to": "2025-01-01T00:00:00"},
            ):
                assert client.post("/api/admin/clock", json=body).status_code == 422
            for value in ("NaN", "Infinity", "-Infinity"):
                response = client.post(
                    "/api/admin/clock",
                    content=f'{{"advance_seconds": {value}}}',
                    headers={"content-type": "application/json"},
                )
                assert response.status_code == 422
                assert response.json()["detail"]
            assert client.get("/api/admin/clock").json()["now"] == now
            assert client.get("/api/admin/events?limit=100").json() == events
            assert client.post("/api/admin/test-flow/step").json()["reconciled"] == 1
            client.post("/api/admin/clock", json={"advance_seconds": RECONCILE_FLOOR.total_seconds()})
            step = client.post("/api/admin/test-flow/step").json()
            assert step["reconciled"] == 1
            assert step["now"] == client.get("/api/admin/clock").json()["now"]
            target = datetime.fromisoformat(step["now"]) + timedelta(seconds=1)
            forward = client.post("/api/admin/clock", json={"to": target.astimezone(UTC).isoformat()})
            assert forward.json() == {"previous": step["now"], "now": target.isoformat()}
            assert client.post("/api/admin/test-flow/advance", json={"seconds": 1}).status_code in (404, 405)
            assert "/api/admin/clock" not in client.get("/openapi.json").json()["paths"]
    finally:
        app.state.engine.dispose()


@pytest.mark.parametrize("legacy", [False, True])
def test_built_board_does_not_mask_unmounted_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    monkeypatch.delenv("BLIZZARD_CLOCK", raising=False)
    monkeypatch.delenv("BLIZZARD_CLOCK_FENCE", raising=False)
    for name in ("BLIZZARD_TEST_FLOW_STEP", "BLIZZARD_CRASH_FENCE"):
        if legacy:
            monkeypatch.setenv(name, "1")
        else:
            monkeypatch.delenv(name, raising=False)
    board = tmp_path / "board"
    board.mkdir()
    (board / "index.html").write_text("<html>board shell</html>")
    monkeypatch.setattr(Frontend, "embedded", Mock(return_value=Frontend(board, "board")))
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    try:
        with TestClient(app) as client:
            for path in ("/api/admin/clock", "/api/admin/test-flow/step", "/api/admin/test-flow/advance"):
                assert client.get(path).status_code == 404
                if not legacy or path.endswith("clock"):
                    assert client.post(path, json={"seconds": 0}).status_code == 404
            assert client.post("/api/admin/test-flow/advance", json={"seconds": 0}).status_code == (
                200 if legacy else 404
            )
            assert client.get("/some/board/route").text == "<html>board shell</html>"
            assert client.get("/api/unrelated-missing").text == "<html>board shell</html>"
    finally:
        app.state.engine.dispose()


@pytest.mark.parametrize(
    "clock,fence,step,crash",
    [
        (None, None, None, None),
        ("controlled", None, None, None),
        ("controlled", "true", None, None),
        (None, "1", None, None),
        (None, None, "1", "1"),
    ],
)
def test_unpermitted_clock_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: str | None,
    fence: str | None,
    step: str | None,
    crash: str | None,
) -> None:
    for name, value in zip(
        ("BLIZZARD_CLOCK", "BLIZZARD_CLOCK_FENCE", "BLIZZARD_TEST_FLOW_STEP", "BLIZZARD_CRASH_FENCE"),
        (clock, fence, step, crash),
        strict=True,
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    app = build_hosted_hub_app(init_hub(tmp_path / "hub"))
    try:
        with TestClient(app) as client:
            assert not any(isinstance(route, APIRoute) and route.path == "/api/admin/clock" for route in app.routes)
            assert client.post("/api/admin/clock", json={"advance_seconds": 0}).status_code in (404, 405)
            assert (client.post("/api/admin/test-flow/advance", json={"seconds": 0}).status_code == 200) == (
                step == "1" and crash == "1"
            )
    finally:
        app.state.engine.dispose()


def test_authorization(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    clock = ControlledClock(datetime(2025, 1, 1, tzinfo=UTC))
    lock = asyncio.Lock()
    app = create_hub_app(
        hub.wired_app.state.config,
        services=hub.hub_services,
        test_flow_router=controlled_flow_router(hub.flow_driver, clock, lock),
        controlled_clock_router=clock_router(clock, lock),
    )
    tenant = seed_session(hub, seed_user(hub, username="tenant-admin", role=Role.ADMIN))
    user = seed_user(hub, username="hub-admin", role=Role.PENDING)
    seed_hub_admin(hub.engine, user.user_id)
    admin = seed_session(hub, user)
    seed_runner(hub, "r1")
    with TestClient(app) as client:
        for method, url, body in (
            ("GET", "/api/admin/clock", None),
            ("POST", "/api/admin/clock", {"advance_seconds": 0}),
            ("POST", "/api/admin/test-flow/step", None),
        ):
            for token, expected in ((None, 401), (tenant, 403), (runner_token("r1"), 403), (admin, 200)):
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                assert client.request(method, url, json=body, headers=headers).status_code == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_step_serializes_move_but_not_read(cancel: bool) -> None:
    clock = ControlledClock(datetime(2025, 1, 1, tzinfo=UTC))
    lock = asyncio.Lock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def held() -> TickResult:
        entered.set()
        await release.wait()
        return TickResult()

    driver = Mock(spec=FlowDriver)
    driver.step = AsyncMock(side_effect=held)
    step = next(r.endpoint for r in controlled_flow_router(driver, clock, lock).routes if isinstance(r, APIRoute))
    routes = clock_router(clock, lock).routes
    read = next(r.endpoint for r in routes if isinstance(r, APIRoute) and r.methods and "GET" in r.methods)
    move = next(r.endpoint for r in routes if isinstance(r, APIRoute) and r.methods and "POST" in r.methods)
    initial = clock.now().isoformat()
    stepping = asyncio.create_task(step())
    await asyncio.wait_for(entered.wait(), 5)
    if cancel:
        stepping.cancel()
    moving = asyncio.create_task(move(ClockMoveRequest(advance_seconds=1)))
    await asyncio.sleep(0)
    if cancel:
        stepping.cancel()
        await asyncio.sleep(0)
    assert not moving.done()
    assert (await read()).now == initial
    release.set()
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await stepping
    else:
        assert (await stepping)["now"] == initial
    assert (await moving).previous == initial
