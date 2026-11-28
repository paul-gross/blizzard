"""Readiness — the ``/api/ready`` probe: the real SQLAlchemy store-status reader over a real migrated store, through
the HTTP surface."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.support.daemons import Daemon


def test_ready_probe_true_against_migrated_store(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.init(tmp_path)
    app = daemon.build_hosted_app(config)
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["store_reachable"] is True
    assert body["store_revision"] == body["expected_revision"]


def test_ready_probe_false_on_unmigrated_store(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.init(tmp_path)
    # Roll the store behind the code's head — a version skew the probe must surface.
    daemon.runtime.migration_runner(config).downgrade("base")
    app = daemon.build_hosted_app(config)
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["store_reachable"] is True


def test_ready_probe_false_when_store_free(daemon: Daemon) -> None:
    # The export/unit app builds without a readiness service wired; the probe is honest.
    app = daemon.build_app()
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    expected: dict[str, object] = {
        "ready": False,
        "store_reachable": False,
        "store_revision": None,
        "expected_revision": None,
        "detail": "readiness service not wired (store-free app)",
    }
    if daemon.name == "runner":
        expected["runners"] = [{"name": "runner-local", "runner_id": "runner-local", "state": "serving"}]
    assert response.json() == expected
