"""An exception no handler maps: the hub logs one structlog line naming the request and the tenant it resolved,
then answers the plain ``500`` Starlette's server-error middleware answers."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import structlog
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import tenant_bound
from tests.support import HubHarness, build_hub, default_tenant, seed_session
from tests.support import seed_user as _seed_user

pytestmark = pytest.mark.component

_EVENT = "unhandled request error"


class _Boom(Exception):
    pass


def _mount_failing_routes(hub: HubHarness) -> TestClient:
    """Two routes raising :class:`_Boom` — one tenant-bound, one resolving no tenant — ahead of the web mount."""
    app = hub.wired_app
    router = APIRouter()

    @router.get("/api/boom-tenant", dependencies=[Depends(tenant_bound)])
    def boom_tenant() -> None:
        raise _Boom("tenant side")

    @router.get("/api/boom-bare")
    def boom_bare() -> None:
        raise _Boom("no tenant")

    before = len(app.router.routes)
    app.include_router(router)
    added = app.router.routes[before:]
    app.router.routes[:] = [*added, *app.router.routes[:before]]
    return TestClient(app, raise_server_exceptions=False)


def _signed_in(hub: HubHarness) -> dict[str, str]:
    user = _seed_user(hub, username="ada", role=Role.ADMIN)
    return {"Cookie": f"bz_session={seed_session(hub, user)}"}


def test_a_tenant_bound_failure_logs_one_line_naming_the_request_and_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    client = _mount_failing_routes(hub)
    headers = _signed_in(hub)

    with capture_logs() as logs:
        resp = client.get("/api/boom-tenant", headers=headers)

    lines = [line for line in logs if line["event"] == _EVENT]
    assert len(lines) == 1
    line = lines[0]
    assert line["log_level"] == "error"
    assert line["tenant_id"] == default_tenant(hub.engine)
    assert (line["method"], line["path"], line["exception_class"]) == ("GET", "/api/boom-tenant", "_Boom")
    assert isinstance(line["exc_info"], _Boom)
    assert (resp.status_code, resp.text, resp.headers["content-type"]) == (
        500,
        "Internal Server Error",
        "text/plain; charset=utf-8",
    )


def test_a_failure_on_a_route_resolving_no_tenant_logs_the_line_without_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    client = _mount_failing_routes(hub)

    with capture_logs() as logs:
        resp = client.get("/api/boom-bare")

    lines = [line for line in logs if line["event"] == _EVENT]
    assert len(lines) == 1
    assert "tenant_id" not in lines[0]
    assert lines[0]["path"] == "/api/boom-bare"
    assert resp.status_code == 500


def test_the_json_line_carries_the_traceback_in_exception(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    client = _mount_failing_routes(hub)
    headers = _signed_in(hub)
    sink = io.StringIO()
    saved = structlog.get_config()
    # The real JSON chain's exception and render steps, onto a sink the test reads.
    structlog.configure(
        processors=[structlog.processors.format_exc_info, structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(file=sink),
        cache_logger_on_first_use=False,
    )
    try:
        client.get("/api/boom-tenant", headers=headers)
    finally:
        structlog.configure(**saved)

    lines = [json.loads(raw) for raw in sink.getvalue().splitlines()]
    (line,) = [line for line in lines if line["event"] == _EVENT]
    assert line["exception_class"] == "_Boom"
    assert "Traceback (most recent call last)" in line["exception"]
    assert "_Boom: tenant side" in line["exception"]
