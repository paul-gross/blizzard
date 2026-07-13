"""App-boot smoke for both daemons (unit tier).

Each daemon's FastAPI app boots without a store, serves ``/api/health``, and
serves the embedded frontend placeholder at ``/`` through the SPA mount seam.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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


def test_spa_fallback_serves_index_for_client_route(daemon: Daemon) -> None:
    app = daemon.build_app()
    with TestClient(app) as client:
        # A deep client-side route the server does not know must resolve to the SPA shell.
        response = client.get("/board/some-chunk-id")
    assert response.status_code == 200
    assert f"blizzard-{daemon.name}" in response.text
