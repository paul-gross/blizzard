"""App-boot smoke for both daemons (unit tier).

Each daemon's FastAPI app boots without a store, serves ``/api/health``, and
serves the embedded frontend placeholder at ``/`` through the SPA mount seam.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.foundation.web import Frontend
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


def test_health_reports_the_installed_version_not_a_literal(daemon: Daemon) -> None:
    """``/api/health`` reports ``importlib.metadata.version("blizzard")``, and the
    OpenAPI document's version must agree with it."""
    app = daemon.build_app()
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["version"] == importlib.metadata.version("blizzard")
    assert app.version == body["version"], "the OpenAPI document and /api/health must agree"


def test_frontend_mount_serves_placeholder(daemon: Daemon, tmp_path: Path) -> None:
    # An EMPTY static dir keeps this hermetic, independent of a real build's
    # (gitignored) static dirs.
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    app = FastAPI()
    Frontend(static_dir, app_name=f"blizzard-{daemon.name}").mount(app)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert f"blizzard-{daemon.name}" in response.text


def test_spa_fallback_serves_index_for_client_route(tmp_path: Path) -> None:
    # A minimal static dir keeps this independent of a real build's (gitignored)
    # static dirs.
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<app-root></app-root>")
    app = FastAPI()
    Frontend(static_dir, app_name="blizzard-hub").mount(app)
    with TestClient(app) as client:
        response = client.get("/board/some-chunk-id")
    assert response.status_code == 200
    assert "<app-root>" in response.text
