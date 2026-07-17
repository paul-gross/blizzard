"""Readiness — the store-status seam, its domain rule, and the ``/api/ready`` probe.

Three levels, one seam:
- **unit** (``evaluate_readiness``): the pure rule against a fake reader — reachable
  and at-head is ready; unreachable or drifted is not — no database.
- **component** (``/api/ready`` over ``build_hosted_app``): the real SQLAlchemy
  store-status reader wired through the composition root against a real migrated
  sqlite store, exercised through the app's HTTP surface — a double only at no seam.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.store.status import StoreStatus
from blizzard.hub.domain.readiness import ReadinessService, evaluate_readiness
from tests.conftest import Daemon


class _FakeReader:
    """A store-status reader that returns a canned reading (unit-tier double)."""

    def __init__(self, status: StoreStatus) -> None:
        self._status = status

    def read_status(self) -> StoreStatus:
        return self._status


@pytest.mark.unit
def test_ready_when_reachable_and_at_head() -> None:
    r = evaluate_readiness(
        StoreStatus(reachable=True, revision="20260713_1112_hub_initial"), expected_revision="20260713_1112_hub_initial"
    )
    assert r.ready is True
    assert r.store_reachable is True
    assert r.store_revision == "20260713_1112_hub_initial"


@pytest.mark.unit
def test_not_ready_when_store_unreachable() -> None:
    r = evaluate_readiness(
        StoreStatus(reachable=False, revision=None, detail="boom"), expected_revision="20260713_1112_hub_initial"
    )
    assert r.ready is False
    assert r.store_reachable is False
    assert "boom" in r.detail


@pytest.mark.unit
def test_not_ready_on_revision_drift() -> None:
    r = evaluate_readiness(StoreStatus(reachable=True, revision=None), expected_revision="20260713_1112_hub_initial")
    assert r.ready is False
    assert "expected 20260713_1112_hub_initial" in r.detail


@pytest.mark.unit
def test_readiness_service_composes_reader_and_expected() -> None:
    service = ReadinessService(
        reader=_FakeReader(StoreStatus(reachable=True, revision="abc")),
        expected_revision="abc",
    )
    assert service.evaluate().ready is True


@pytest.mark.component
def test_ready_probe_true_against_migrated_store(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.runtime.init_environment(tmp_path)
    app = daemon.build_hosted_app(config)
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["store_reachable"] is True
    assert body["store_revision"] == body["expected_revision"]


@pytest.mark.component
def test_ready_probe_false_on_unmigrated_store(daemon: Daemon, tmp_path: Path) -> None:
    config = daemon.runtime.init_environment(tmp_path)
    # Roll the store behind the code's head — a version skew the probe must surface.
    daemon.runtime.migration_runner(config).downgrade("base")
    app = daemon.build_hosted_app(config)
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["store_reachable"] is True


@pytest.mark.component
def test_ready_probe_false_when_store_free(daemon: Daemon) -> None:
    # The export/unit app builds without a readiness service wired; the probe is honest.
    app = daemon.build_app()
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert "not wired" in body["detail"]
