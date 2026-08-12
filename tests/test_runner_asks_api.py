"""``POST /api/leases/{id}/asks`` (issue #51) — token-authorized like every other worker
verb (issue #291), plus its open-lane ``GET /api/asks`` counterpart.

Exercised over a real store via TestClient: the route's 403/404/503 forms and the
worker-authorization resolver's second half, an open takeover naming a closed lease."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.domain.enrollment import TokenHash
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import make_store

_NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"


def _app_with_store(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    return create_app(config, runner_store=store), store


def _seed_lease(store, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "runner_id": "runner-local",
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))  # type: ignore[arg-type]
    store.record_lease_token(str(fields["lease_id"]), TokenHash(_TOKEN).hex, _NOW)


@pytest.mark.component
def test_503_when_store_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        resp = client.post(
            "/api/leases/lease_1/asks", json={"question": "q?"}, headers={"X-Blizzard-Lease-Token": _TOKEN}
        )
    assert resp.status_code == 503


@pytest.mark.component
def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_store(tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_ghost/asks", json={"question": "q?"}, headers={"X-Blizzard-Lease-Token": _TOKEN}
        )
    assert resp.status_code == 404


@pytest.mark.component
def test_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post("/api/leases/lease_1/asks", json={"question": "q?"})
    assert resp.status_code == 403
    assert store.unforwarded_ask("lease_1") is None


@pytest.mark.component
def test_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/asks", json={"question": "q?"}, headers={"X-Blizzard-Lease-Token": "nope"}
        )
    assert resp.status_code == 403
    assert store.unforwarded_ask("lease_1") is None


@pytest.mark.component
def test_200_records_the_ask_with_the_dedicated_header(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/asks",
            json={"question": "which way?", "options": ["a", "b"]},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["recorded"] is True
    assert body["lease_id"] == "lease_1"
    ask = store.unforwarded_ask("lease_1")
    assert ask is not None
    assert ask.question == "which way?"
    assert ask.options == ["a", "b"]


@pytest.mark.component
def test_a_closed_lease_is_404_not_403(tmp_path: Path) -> None:
    """A lease's own token still hashes correctly once closed — 404 (unknown/closed)
    takes precedence over ever reaching the token check."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/asks", json={"question": "q?"}, headers={"X-Blizzard-Lease-Token": _TOKEN}
        )
    assert resp.status_code == 404


@pytest.mark.component
def test_an_open_takeover_authorizes_a_closed_reference_lease(tmp_path: Path) -> None:
    """The resolver's other half (issue #291): an open takeover's re-minted token
    authorizes its closed reference lease the same as an active lease's token would —
    previously this route checked no token at all."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    takeover_token = "the-takeover-token"
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    store.record_lease_token("lease_1", TokenHash(takeover_token).hex, _NOW)

    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/asks", json={"question": "q?"}, headers={"X-Blizzard-Lease-Token": takeover_token}
        )
    assert resp.status_code == 201, resp.text
    assert store.unforwarded_ask("lease_1") is not None
