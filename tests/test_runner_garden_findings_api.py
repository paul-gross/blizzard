"""``GET /api/leases/{id}/garden/findings`` — the lease-scoped, hub-proxying route (D5,
component tier). Authorization mirrors ``tests/test_runner_chunk_history_api.py``'s own
shape: the hub is never consulted for an unauthorized caller. The route itself carries
no flag naming a routine or a scope — there is nothing here for a worker to point at
another routine's bucket."""

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

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_HUB_URL = "http://hub.local:8421"
_CHUNK = "ch_1"

_BUCKET = [
    {
        "finding_id": "fin_1",
        "routine_name": "nightly",
        "scope_slug": "blizzard",
        "class": "stale-docstring",
        "locus": "a.py:1",
        "summary": "s",
        "introduced": None,
        "live": True,
        "state": "live",
        "note": None,
        "last_seen_at": None,
        "observed_count": 0,
    }
]


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


def test_503_when_store_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    with TestClient(create_app(config)) as client:
        resp = client.get("/api/leases/lease_1/garden/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 503


def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_ghost/garden/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


def test_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/garden/findings")
    assert resp.status_code == 403


def test_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/garden/findings", headers={"X-Blizzard-Lease-Token": "nope"})
    assert resp.status_code == 403


def test_a_closed_lease_is_404_not_403(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    store.record_closure(
        lease_id="lease_1", chunk_id=_CHUNK, node_id="nd_reconcile", reason="transitioned", closed_at=_NOW
    )
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/garden/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


def test_503_when_hub_unwired_even_for_an_authorized_lease(tmp_path: Path) -> None:
    """Authorization is resolved before the hub is consulted, so an unauthorized caller
    never learns the hub-wiring state — mirrors the artifacts and history proxies."""
    app, store = _app_with_store(tmp_path, hub_url="")
    _seed_lease(store)
    with TestClient(app) as client:
        authed = client.get("/api/leases/lease_1/garden/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
        unauthed = client.get("/api/leases/lease_1/garden/findings", headers={"X-Blizzard-Lease-Token": "nope"})
    assert authed.status_code == 503
    assert unauthed.status_code == 403


def test_forwards_to_the_hub_chunks_garden_findings_route_and_returns_the_bucket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(monkeypatch, _FakeHubResponse(200, _BUCKET), seen)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/garden/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}/garden/findings"]
    assert resp.json() == _BUCKET


def test_forwards_the_runner_bearer_when_a_token_is_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The forward rides the runner principal's bearer — the worker's own lease token
    never leaves the runner, and no `BZ_HUB_URL` is ever named by the worker's own call."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(
        root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=_HUB_URL, hub_token="hub-tok"
    )
    _seed_lease(store)
    seen_headers: list[dict[str, str]] = []

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _BUCKET)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    with TestClient(create_app(config, runner_stores=make_stores(store))) as client:
        resp = client.get("/api/leases/lease_1/garden/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen_headers == [{"Authorization": "Bearer hub-tok"}]


def test_502_when_the_hub_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    app, store = _app_with_store(tmp_path)
    _seed_lease(store)

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/garden/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 502


def test_hub_refusal_of_a_non_routine_chunk_passes_through_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lease on a chunk that is not a routine run gets a legible refusal, not an empty
    list — the hub's own 404 forwarded as-is."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(monkeypatch, _FakeHubResponse(404, {"detail": f"chunk {_CHUNK} carries no run context"}))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/garden/findings", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404
    assert "no run context" in resp.json()["detail"]
