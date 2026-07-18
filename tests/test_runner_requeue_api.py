"""``POST /chunks/{id}/requeues`` (issue #53).

Exercised over a real store via TestClient, mirroring
``tests/test_runner_takeover_api.py``'s convention: the route's shape, its 409/503
forms, and the store-derivation it delegates to (:class:`RequeueService`, pinned at
the domain level by ``tests/test_runner_requeue.py``) are the point here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.requeue import RequeueService
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import make_store

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
_LATER = datetime(2026, 7, 17, 13, 0, 0, tzinfo=UTC)  # strictly after the seeded lease's _NOW


def _app_with_requeue(tmp_path: Path, *, clock: FixedClock | None = None):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = RequeueService(store, clock or FixedClock(_LATER))
    return create_app(config, runner_store=store, requeue=service), store


def _seed_escalated_chunk(store) -> None:  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="runner-local",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)


@pytest.mark.component
def test_503_when_requeue_service_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        assert client.post("/api/chunks/ch_1/requeues").status_code == 503


@pytest.mark.component
def test_requeue_over_an_escalated_chunk_returns_202(tmp_path: Path) -> None:
    app, store = _app_with_requeue(tmp_path)
    _seed_escalated_chunk(store)

    with TestClient(app) as client:
        resp = client.post("/api/chunks/ch_1/requeues")

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"chunk_id": "ch_1", "requeued": True}
    assert "ch_1" in store.pending_requeue_chunk_ids()


@pytest.mark.component
def test_requeue_while_a_takeover_is_open_is_409(tmp_path: Path) -> None:
    app, store = _app_with_requeue(tmp_path)
    _seed_escalated_chunk(store)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )

    with TestClient(app) as client:
        resp = client.post("/api/chunks/ch_1/requeues")

    assert resp.status_code == 409, resp.text
    assert store.pending_requeue_chunk_ids() == set()


@pytest.mark.component
def test_requeue_a_chunk_that_is_not_needs_human_is_409(tmp_path: Path) -> None:
    app, store = _app_with_requeue(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="runner-local",
            retries_max=2,
            created_at=_NOW,
        )
    )  # active, never closed — not needs_human

    with TestClient(app) as client:
        resp = client.post("/api/chunks/ch_1/requeues")

    assert resp.status_code == 409, resp.text


@pytest.mark.component
def test_requeue_after_an_ended_takeover_returns_202(tmp_path: Path) -> None:
    """The pasted-command flow: a takeover opened and ended over the escalated chunk."""
    app, store = _app_with_requeue(tmp_path)
    _seed_escalated_chunk(store)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    store.record_takeover_end(takeover_id="tko_1", ended_at=_NOW)

    with TestClient(app) as client:
        resp = client.post("/api/chunks/ch_1/requeues")

    assert resp.status_code == 202, resp.text
    assert "ch_1" in store.pending_requeue_chunk_ids()
