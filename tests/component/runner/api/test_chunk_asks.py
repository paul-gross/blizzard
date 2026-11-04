"""``GET /api/leases/{id}/asks``.

The lease-scoped route over a real store via ``TestClient``, the hub reached through a stubbed ``httpx2.Client``."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Unpack

import httpx2
import pytest
from fastapi import FastAPI

from blizzard.foundation.tokens import TokenHash
from blizzard.runner.config import RunnerConfig
from blizzard.runner.leases.model import NewLease
from tests.config_inputs import create_runner_app
from tests.factory_fields import NewLeaseFields
from tests.runner_client import runner_client
from tests.runner_fakes import SqlAlchemyRunnerStore, make_host_stores, make_store, make_stores, no_retry_clock
from tests.support_chunk_asks_api import CHUNK, DETAIL

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


_TOKEN = "the-lease-token"


_HUB_URL = "http://hub.local:8421"


class _HubRouter:
    """A late-bound handler behind the proxy's ``httpx2.Client`` — ``_app_with_store`` builds
    the client (and the app wired to it) before a test knows how the hub should answer, so
    ``_stub_hub`` arms this after the fact instead of monkeypatching a module-level function."""

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


def _seed_lease(store: SqlAlchemyRunnerStore, **overrides: Unpack[NewLeaseFields]) -> None:
    fields: NewLeaseFields = {
        "lease_id": "lease_1",
        "chunk_id": CHUNK,
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 3,
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))
    store.record_lease_token(fields["lease_id"], TokenHash(_TOKEN).hex, _NOW)


def _stub_hub(app: FastAPI, status_code: int, payload: object, seen: list[str] | None = None) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx2.Response(status_code, json=payload)

    app.state.hub_router.handler = handler


def test_503_when_store_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    with runner_client(create_runner_app(config)) as client:
        resp = client.get("/api/leases/lease_1/asks", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 503


def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_store(tmp_path)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_ghost/asks", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-Blizzard-Lease-Token": "nope"}])
def test_403_for_a_missing_or_wrong_token_before_the_hub_is_contacted(tmp_path: Path, headers: dict[str, str]) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(app, 200, DETAIL, seen)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/asks", headers=headers)
    assert resp.status_code == 403
    assert seen == []


def test_503_when_hub_unwired_even_for_an_authorized_lease(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path, hub_url="")
    _seed_lease(store)
    with runner_client(app) as client:
        authed = client.get("/api/leases/lease_1/asks", headers={"X-Blizzard-Lease-Token": _TOKEN})
        unauthed = client.get("/api/leases/lease_1/asks", headers={"X-Blizzard-Lease-Token": "nope"})
    assert authed.status_code == 503
    assert unauthed.status_code == 403


def test_reads_the_leases_own_chunk_and_returns_the_rows(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(app, 200, DETAIL, seen)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/asks", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{CHUNK}"]
    body = resp.json()
    assert [r["question_id"] for r in body] == ["q1", "q2", "q3"]
    assert body[0]["node"] == "build" and body[0]["answer"] == "a" and body[0]["answered_by"] == "paul"
    assert "session_id" not in body[0] and "runner_id" not in body[0]


def test_502_when_the_hub_is_unreachable(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    app.state.hub_router.handler = handler
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/asks", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 502
