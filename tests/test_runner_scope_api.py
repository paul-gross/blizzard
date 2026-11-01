"""``GET /api/leases/{id}/scopes`` — the lease-scoped, hub-proxying route
(component tier). Authorization mirrors ``tests/test_runner_garden_findings_api.py``'s
own shape: the hub is never consulted for an unauthorized caller. The read names the
lease's chunk, so the hub answers that chunk's project's scopes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from fastapi import FastAPI

from blizzard.foundation.tokens import TokenHash
from blizzard.runner.config import RunnerConfig
from blizzard.runner.leases.model import NewLease
from tests.config_inputs import create_runner_app
from tests.runner_client import runner_client
from tests.runner_fakes import SqlAlchemyRunnerStore, make_host_stores, make_store, make_stores, no_retry_clock

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_HUB_URL = "http://hub.local:8421"
_CHUNK = "ch_1"

_SCOPES = [{"slug": "blizzard", "description": "", "created_at": "2026-01-01T00:00:00Z", "retired": False}]


class _HubRouter:
    def __init__(self) -> None:
        self.handler: Callable[[httpx2.Request], httpx2.Response] = lambda request: httpx2.Response(
            500, json={"detail": f"hub not stubbed for {request.url}"}
        )

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        return self.handler(request)


def _app_with_store(tmp_path: Path, *, hub_url: str = _HUB_URL):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=hub_url)
    router = _HubRouter()
    app = create_runner_app(
        config,
        runner_stores=make_stores(store),
        host_stores=make_host_stores(store),
        hub_proxy_client=httpx2.Client(transport=httpx2.MockTransport(router)),
        hub_retry_clock=no_retry_clock(),
    )
    app.state.hub_router = router
    return app, store


def _seed_lease(store: SqlAlchemyRunnerStore, **overrides: object) -> None:
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": _CHUNK,
        "graph_id": "gr_1",
        "node_id": "nd_reconcile",
        "node_name": "reconcile",
        "epoch": 1,
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))  # type: ignore[arg-type]
    store.record_lease_token(str(fields["lease_id"]), TokenHash(_TOKEN).hex, _NOW)


def _stub_hub(app: FastAPI, status_code: int, payload: object, seen: list[str] | None = None) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx2.Response(status_code, json=payload)

    app.state.hub_router.handler = handler


@pytest.mark.component
def test_503_when_store_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    with runner_client(create_runner_app(config)) as client:
        resp = client.get("/api/leases/lease_1/scopes", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 503


@pytest.mark.component
def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_store(tmp_path)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_ghost/scopes", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


@pytest.mark.component
def test_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/scopes")
    assert resp.status_code == 403


@pytest.mark.component
def test_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/scopes", headers={"X-Blizzard-Lease-Token": "nope"})
    assert resp.status_code == 403


@pytest.mark.component
def test_503_when_hub_unwired_even_for_an_authorized_lease(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path, hub_url="")
    _seed_lease(store)
    with runner_client(app) as client:
        authed = client.get("/api/leases/lease_1/scopes", headers={"X-Blizzard-Lease-Token": _TOKEN})
        unauthed = client.get("/api/leases/lease_1/scopes", headers={"X-Blizzard-Lease-Token": "nope"})
    assert authed.status_code == 503
    assert unauthed.status_code == 403


@pytest.mark.component
def test_forwards_to_the_hub_fleet_scopes_route_and_returns_the_vocabulary(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(app, 200, _SCOPES, seen)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/scopes", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen == [f"{_HUB_URL}/api/fleet/scopes?chunk_id={_CHUNK}"]
    assert resp.json() == [
        {**scope, "revision": None, "project_id": None, "project_slug": None, "project_name": None} for scope in _SCOPES
    ]
