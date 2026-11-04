"""Readiness — the store-status seam and its domain rule (``Readiness.of``): the pure rule against a fake reader —
reachable and at-head is ready, unreachable or drifted is not."""

from __future__ import annotations

from blizzard.foundation.store.readiness import Readiness, ReadinessService
from blizzard.foundation.store.status import StoreStatus


class _FakeReader:
    """A store-status reader that returns a canned reading (unit-tier double)."""

    def __init__(self, status: StoreStatus) -> None:
        self._status = status

    def read_status(self) -> StoreStatus:
        return self._status


def test_ready_when_reachable_and_at_head() -> None:
    r = Readiness.of(
        StoreStatus(reachable=True, revision="20260713_1112_hub_initial"), expected_revision="20260713_1112_hub_initial"
    )
    assert r.ready is True
    assert r.store_reachable is True
    assert r.store_revision == "20260713_1112_hub_initial"


def test_not_ready_when_store_unreachable() -> None:
    r = Readiness.of(
        StoreStatus(reachable=False, revision=None, detail="boom"), expected_revision="20260713_1112_hub_initial"
    )
    assert r.ready is False
    assert r.store_reachable is False
    assert "boom" in r.detail


def test_not_ready_on_revision_drift() -> None:
    r = Readiness.of(StoreStatus(reachable=True, revision=None), expected_revision="20260713_1112_hub_initial")
    assert r.ready is False
    assert "expected 20260713_1112_hub_initial" in r.detail


def test_readiness_service_composes_reader_and_expected() -> None:
    service = ReadinessService(
        reader=_FakeReader(StoreStatus(reachable=True, revision="abc")),
        expected_revision="abc",
    )
    assert service.evaluate().ready is True
