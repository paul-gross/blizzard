"""``GET /api/leases/{id}/findings`` and its ``/{finding_id}`` sibling — the lease-scoped,
hub-proxying routes (blizzard#397 Phase 2, component tier). Authorization mirrors
``tests/test_runner_garden_findings_api.py``'s own shape: the hub is never consulted for
an unauthorized caller. Neither verb accepts a flag naming another chunk, routine, or
scope."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import blizzard.runner.api.hub_proxy as hub_proxy
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import NewLease
from tests.runner_fakes import make_store, make_stores

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_HUB_URL = "http://hub.local:8421"
_CHUNK = "ch_1"

_FINDING = {
    "finding_id": "fin_1",
    "routine_name": "nightly",
    "scope_slug": "blizzard",
    "class": "stale-docstring",
    "locus": "a.py:1",
    "summary": "s",
    "introduced": None,
    "introduced_at": None,
    "first_observed_at": None,
    "live": True,
    "state": "live",
    "note": None,
    "last_seen_at": None,
    "observed_count": 0,
}
_BUCKET = [_FINDING]


class _FakeHubResponse:
    def __init__(self, status_code: int, payload: object | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def _app_with_store(tmp_path: Path, *, hub_url: str = _HUB_URL):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=hub_url)
    return create_app(config, runner_stores=make_stores(store)), store


def _seed_lease(store, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": _CHUNK,
        "graph_id": "gr_1",
        "node_id": "nd_reconcile",
        "node_name": "reconcile",
        "epoch": 1,
        "runner_id": "runner-local",
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))  # type: ignore[arg-type]
    store.record_lease_token(str(fields["lease_id"]), TokenHash(_TOKEN).hex, _NOW)


def _stub_hub(monkeypatch: pytest.MonkeyPatch, response: _FakeHubResponse, seen: list[str] | None = None) -> None:
    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        if seen is not None:
            seen.append(url)
        return response

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)


# --------------------------------------------------------------------------- #
# GET /leases/{id}/findings


def test_list_503_when_store_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    with TestClient(create_app(config)) as client:
        resp = client.get("/api/leases/lease_1/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 503


def test_list_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_ghost/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


def test_list_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/findings")
    assert resp.status_code == 403


def test_list_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/findings", headers={"X-Blizzard-Lease-Token": "nope"})
    assert resp.status_code == 403


def test_list_503_when_hub_unwired_even_for_an_authorized_lease(tmp_path: Path) -> None:
    """Authorization is resolved before the hub is consulted, so an unauthorized caller
    never learns the hub-wiring state."""
    app, store = _app_with_store(tmp_path, hub_url="")
    _seed_lease(store)
    with TestClient(app) as client:
        authed = client.get("/api/leases/lease_1/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
        unauthed = client.get("/api/leases/lease_1/findings", headers={"X-Blizzard-Lease-Token": "nope"})
    assert authed.status_code == 503
    assert unauthed.status_code == 403


def test_list_forwards_to_the_hub_chunks_findings_route_and_returns_the_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(monkeypatch, _FakeHubResponse(200, _BUCKET), seen)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}/findings"]
    assert resp.json() == _BUCKET


def test_list_502_when_the_hub_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    app, store = _app_with_store(tmp_path)
    _seed_lease(store)

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 502


def test_list_hub_refusal_of_a_chunk_answering_no_proposal_passes_through_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(monkeypatch, _FakeHubResponse(404, {"detail": f"chunk {_CHUNK} answers no accepted, minted proposal"}))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404
    assert "no accepted, minted proposal" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# GET /leases/{id}/findings/{finding_id}


def test_get_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/findings/fin_1")
    assert resp.status_code == 403


def test_get_forwards_to_the_hub_chunks_finding_route_and_returns_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(monkeypatch, _FakeHubResponse(200, _FINDING), seen)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/findings/fin_1", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}/findings/fin_1"]
    assert resp.json() == _FINDING


def test_get_hub_refusal_of_an_out_of_set_id_passes_through_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(monkeypatch, _FakeHubResponse(404, {"detail": "finding fin_other is not among the findings"}))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/findings/fin_other", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404
    assert "not among the findings" in resp.json()["detail"]
