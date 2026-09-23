"""``GET /api/leases/{id}/analytics/...`` — the lease-scoped, hub-proxying reads of the
six counts/spend summaries (blizzard#545, component tier). Authorization mirrors
``tests/test_runner_garden_findings_api.py``'s own shape: the hub is never consulted for
an unauthorized caller. The proxy forwards the window params unvalidated and returns the
hub body verbatim, including a non-routine chunk's own 404."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.foundation.tokens import TokenHash
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import NewLease
from tests.runner_fakes import make_store, make_stores, no_retry_delay

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_HUB_URL = "http://hub.local:8421"
_CHUNK = "ch_1"

_COUNTS_ROUTES = [
    "counts/files",
    "counts/skills",
    "counts/agent-types",
    "counts/nodes",
]
_SPEND_ROUTES = ["spend/nodes", "spend/graphs"]
_ROUTES = _COUNTS_ROUTES + _SPEND_ROUTES

_HUB_SUFFIX = {
    "counts/files": "analytics/counts/files",
    "counts/skills": "analytics/counts/skills",
    "counts/agent-types": "analytics/counts/agent-types",
    "counts/nodes": "analytics/counts/nodes",
    "spend/nodes": "analytics/spend/nodes",
    "spend/graphs": "analytics/spend/graphs",
}

_COUNTS_BODY = {"counts": [{"key": "a.py", "count": 3}]}
_SPEND_BODY = {
    "spend": [
        {
            "key": "nd_build",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "cache_create_tokens": 5,
            "cost_usd": 0.1,
            "cost_partial": False,
        }
    ]
}


def _body_for(suffix: str) -> dict:
    return _COUNTS_BODY if suffix.startswith("counts/") else _SPEND_BODY


class _HubRouter:
    def __init__(self) -> None:
        self.handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
            500, json={"detail": f"hub not stubbed for {request.url}"}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        return self.handler(request)


def _app_with_store(tmp_path: Path, *, hub_url: str = _HUB_URL):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=hub_url)
    router = _HubRouter()
    app = create_app(
        config,
        runner_stores=make_stores(store),
        hub_proxy_client=httpx.Client(transport=httpx.MockTransport(router)),
        hub_retry_delay=no_retry_delay,
    )
    app.state.hub_router = router
    return app, store


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


def _stub_hub(app: FastAPI, status_code: int, payload: object, seen: list[str] | None = None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(status_code, json=payload)

    app.state.hub_router.handler = handler


@pytest.mark.parametrize("suffix", _ROUTES)
def test_503_when_store_unwired(tmp_path: Path, suffix: str) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    with TestClient(create_app(config)) as client:
        resp = client.get(
            f"/api/leases/lease_1/analytics/{suffix}",
            params={"since": "2020-01-01T00:00:00Z"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 503


@pytest.mark.parametrize("suffix", _ROUTES)
def test_404_for_an_unknown_lease(tmp_path: Path, suffix: str) -> None:
    app, _store = _app_with_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get(
            f"/api/leases/lease_ghost/analytics/{suffix}",
            params={"since": "2020-01-01T00:00:00Z"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 404


@pytest.mark.parametrize("suffix", _ROUTES)
def test_403_for_a_missing_token(tmp_path: Path, suffix: str) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get(f"/api/leases/lease_1/analytics/{suffix}", params={"since": "2020-01-01T00:00:00Z"})
    assert resp.status_code == 403


@pytest.mark.parametrize("suffix", _ROUTES)
def test_403_for_a_wrong_token(tmp_path: Path, suffix: str) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get(
            f"/api/leases/lease_1/analytics/{suffix}",
            params={"since": "2020-01-01T00:00:00Z"},
            headers={"X-Blizzard-Lease-Token": "nope"},
        )
    assert resp.status_code == 403


@pytest.mark.parametrize("suffix", _ROUTES)
def test_503_when_hub_unwired_even_for_an_authorized_lease(tmp_path: Path, suffix: str) -> None:
    """Authorization is resolved before the hub is consulted, so an unauthorized caller
    never learns the hub-wiring state — mirrors the garden-findings proxy."""
    app, store = _app_with_store(tmp_path, hub_url="")
    _seed_lease(store)
    with TestClient(app) as client:
        authed = client.get(
            f"/api/leases/lease_1/analytics/{suffix}",
            params={"since": "2020-01-01T00:00:00Z"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
        unauthed = client.get(
            f"/api/leases/lease_1/analytics/{suffix}",
            params={"since": "2020-01-01T00:00:00Z"},
            headers={"X-Blizzard-Lease-Token": "nope"},
        )
    assert authed.status_code == 503
    assert unauthed.status_code == 403


@pytest.mark.parametrize("suffix", _ROUTES)
def test_forwards_the_window_params_and_returns_the_hub_body(tmp_path: Path, suffix: str) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    body = _body_for(suffix)
    _stub_hub(app, 200, body, seen)
    with TestClient(app) as client:
        resp = client.get(
            f"/api/leases/lease_1/analytics/{suffix}",
            params={"since": "2020-01-01T00:00:00Z", "until": "2030-01-01T00:00:00Z"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 200, resp.text
    assert seen == [
        f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}/{_HUB_SUFFIX[suffix]}"
        f"?since=2020-01-01T00%3A00%3A00Z&until=2030-01-01T00%3A00%3A00Z"
    ]
    assert resp.json() == body


@pytest.mark.parametrize("suffix", _ROUTES)
def test_502_when_the_hub_is_unreachable(tmp_path: Path, suffix: str) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    app.state.hub_router.handler = handler
    with TestClient(app) as client:
        resp = client.get(
            f"/api/leases/lease_1/analytics/{suffix}",
            params={"since": "2020-01-01T00:00:00Z"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 502


@pytest.mark.parametrize("suffix", _ROUTES)
def test_hub_refusal_of_a_non_routine_chunk_passes_through_verbatim(tmp_path: Path, suffix: str) -> None:
    """A lease on a chunk that is not a routine run gets a legible refusal, not an empty
    bucket — the hub's own 404 forwarded as-is."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, 404, {"detail": f"chunk {_CHUNK} carries no run context"})
    with TestClient(app) as client:
        resp = client.get(
            f"/api/leases/lease_1/analytics/{suffix}",
            params={"since": "2020-01-01T00:00:00Z"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 404
    assert "no run context" in resp.json()["detail"]
