"""App-boot smoke for both daemons (unit tier).

Each daemon's FastAPI app boots without a store, serves ``/api/health``, and
serves the embedded frontend placeholder at ``/`` through the SPA mount seam.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.foundation.web import mount_web_app
from tests.conftest import Daemon

# The app boots with real internal collaborators, doubles only at the (absent) seams.
pytestmark = pytest.mark.component


def test_health_endpoint(daemon: Daemon) -> None:
    app = daemon.build_app()
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == f"blizzard-{daemon.name}"


def test_frontend_mount_serves_placeholder(daemon: Daemon) -> None:
    app = daemon.build_app()
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert f"blizzard-{daemon.name}" in response.text


def test_spa_fallback_serves_index_for_client_route(tmp_path: Path) -> None:
    # SPA routing at the mount seam: once a frontend build has landed an index.html,
    # a deep client-side route the server does not know must resolve to that shell
    # (not 404). Build a minimal static dir here so the test exercises the present-
    # index SpaStaticFiles path independent of whether a real build has filled the
    # package's (now fully gitignored) static dirs.
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<app-root></app-root>")
    app = FastAPI()
    mount_web_app(app, static_dir, app_name="blizzard-hub")
    with TestClient(app) as client:
        response = client.get("/board/some-chunk-id")
    assert response.status_code == 200
    assert "<app-root>" in response.text
