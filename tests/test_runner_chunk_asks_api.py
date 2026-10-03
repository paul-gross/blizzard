"""``GET /api/leases/{id}/asks`` and its pure projection, ``runner.api.chunk_asks._rows``.

Unit tier: ``_rows`` — node-label resolution with an id fallback, oldest-first order, and open,
answered, and superseded questions alike. Component tier: the lease-scoped route over a real
store via ``TestClient``, the hub reached through a stubbed ``httpx.Client``."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.foundation.tokens import TokenHash
from blizzard.runner.api.chunk_asks import _rows
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import NewLease
from blizzard.wire.chunk_asks import ChunkAsksSource
from tests.runner_fakes import make_store, make_stores, no_retry_clock

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_HUB_URL = "http://hub.local:8421"
_CHUNK = "ch_1"


def _question(question_id: str, node_id: str | None, asked_at: str, **extra: object) -> dict[str, object]:
    return {
        "question_id": question_id,
        "chunk_id": _CHUNK,
        "node_id": node_id,
        "runner_id": "r1",
        "epoch": 1,
        "question": f"q {question_id}",
        "asked_at": asked_at,
        **extra,
    }


# Deliberately out of oldest-first source order; a detail without ``history`` or ``migrations``.
_DETAIL: dict[str, object] = {
    "chunk_id": _CHUNK,
    "current_node_id": "nd_build",
    "current_node_name": "build",
    "questions": [
        _question(
            "q2",
            "nd_ghost",
            "2026-07-21T11:00:00+00:00",
            answered=True,
            answer="no answer applies",
            answered_by="restart",
            answered_at="2026-07-21T11:30:00+00:00",
        ),
        _question(
            "q1",
            "nd_build",
            "2026-07-21T10:00:00+00:00",
            options=["a", "b"],
            answered=True,
            answer="a",
            answered_by="paul",
            answered_at="2026-07-21T10:30:00+00:00",
        ),
        _question("q3", "nd_build", "2026-07-21T12:00:00+00:00"),
    ],
}


# Unit — the pure projection
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_rows_are_oldest_first_and_list_open_and_answered_alike() -> None:
    rows = _rows(ChunkAsksSource.model_validate(_DETAIL))
    assert [r.question_id for r in rows] == ["q1", "q2", "q3"]
    assert [r.answered for r in rows] == [True, True, False]
    assert rows[0].answer == "a" and rows[0].answered_by == "paul" and rows[0].options == ["a", "b"]
    assert rows[2].answer is None


@pytest.mark.unit
def test_node_label_is_the_name_and_falls_back_to_the_raw_id() -> None:
    rows = _rows(ChunkAsksSource.model_validate(_DETAIL))
    assert rows[0].node == "build"
    assert rows[1].node == "nd_ghost"


@pytest.mark.unit
def test_node_name_resolves_from_history_transitions() -> None:
    detail = {
        **_DETAIL,
        "current_node_name": None,
        "history": [
            {
                "from_node_id": "nd_build",
                "from_node_name": "build",
                "to_node_id": "nd_review",
                "to_node_name": "review",
                "choice_name": "ready",
                "epoch": 1,
                "recorded_at": "2026-07-21T10:00:00+00:00",
            }
        ],
    }
    assert _rows(ChunkAsksSource.model_validate(detail))[0].node == "build"


@pytest.mark.unit
def test_a_question_without_a_node_has_no_label() -> None:
    detail = {"questions": [_question("q1", None, "2026-07-21T10:00:00+00:00")]}
    assert _rows(ChunkAsksSource.model_validate(detail))[0].node is None


# Component — the lease-scoped, hub-proxying route
# --------------------------------------------------------------------------- #


class _HubRouter:
    """A late-bound handler behind the proxy's ``httpx.Client`` — ``_app_with_store`` builds
    the client (and the app wired to it) before a test knows how the hub should answer, so
    ``_stub_hub`` arms this after the fact instead of monkeypatching a module-level function."""

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
        hub_retry_clock=no_retry_clock(),
    )
    app.state.hub_router = router
    return app, store


def _seed_lease(store, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": _CHUNK,
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 3,
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


@pytest.mark.component
@pytest.mark.component
def test_503_when_store_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    with TestClient(create_app(config)) as client:
        resp = client.get("/api/leases/lease_1/asks", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 503


@pytest.mark.component
def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_ghost/asks", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


@pytest.mark.component
@pytest.mark.parametrize("headers", [{}, {"X-Blizzard-Lease-Token": "nope"}])
def test_403_for_a_missing_or_wrong_token_before_the_hub_is_contacted(tmp_path: Path, headers: dict[str, str]) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(app, 200, _DETAIL, seen)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/asks", headers=headers)
    assert resp.status_code == 403
    assert seen == []


@pytest.mark.component
def test_503_when_hub_unwired_even_for_an_authorized_lease(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path, hub_url="")
    _seed_lease(store)
    with TestClient(app) as client:
        authed = client.get("/api/leases/lease_1/asks", headers={"X-Blizzard-Lease-Token": _TOKEN})
        unauthed = client.get("/api/leases/lease_1/asks", headers={"X-Blizzard-Lease-Token": "nope"})
    assert authed.status_code == 503
    assert unauthed.status_code == 403


@pytest.mark.component
def test_reads_the_leases_own_chunk_and_returns_the_rows(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(app, 200, _DETAIL, seen)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/asks", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}"]
    body = resp.json()
    assert [r["question_id"] for r in body] == ["q1", "q2", "q3"]
    assert body[0]["node"] == "build" and body[0]["answer"] == "a" and body[0]["answered_by"] == "paul"
    assert "session_id" not in body[0] and "runner_id" not in body[0]


@pytest.mark.component
def test_502_when_the_hub_is_unreachable(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    app.state.hub_router.handler = handler
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/asks", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 502
