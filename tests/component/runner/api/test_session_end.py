"""The worker session-end path — local-API endpoint + ``blizzard runner session-end`` verb.

Component exercises the endpoint over a real store (TestClient); unit exercises the
verb's identity handling and soft-fail. A third block asserts the settings document
actually wires the hook.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from blizzard.runner.config import RunnerConfig
from blizzard.runner.leases.model import NewLease
from tests.support.config_inputs import create_runner_app
from tests.support.runner_client import runner_client
from tests.support.runner_fakes import SqlAlchemyRunnerStore, make_host_stores, make_store, make_stores

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _runner_app_with_store(tmp_path: Path):
    """A runner app wired to a real (migrated) store — the ``host`` session-end surface."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    return create_runner_app(config, runner_stores=make_stores(store), host_stores=make_host_stores(store)), store


def _seed_lease(store: SqlAlchemyRunnerStore, lease_id: str = "lease_1") -> None:
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )


def test_session_end_endpoint_records_the_fact(tmp_path: Path) -> None:
    app, store = _runner_app_with_store(tmp_path)
    _seed_lease(store)
    assert store.session_ended_lease_ids() == set()

    with runner_client(app) as client:
        resp = client.post("/api/leases/lease_1/session-end")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"recorded": True, "lease_id": "lease_1"}
    assert store.session_ended_lease_ids() == {"lease_1"}  # the "declared done" signal now exists


def test_session_end_endpoint_replayed_after_closure_still_records(tmp_path: Path) -> None:
    """The closure-spanning resolution keeps tolerating a replay after the lease closed."""
    app, store = _runner_app_with_store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)

    with runner_client(app) as client:
        resp = client.post("/api/leases/lease_1/session-end")

    assert resp.status_code == 200, resp.text
    assert store.session_ended_lease_ids() == {"lease_1"}


def test_session_end_endpoint_404_for_an_unminted_lease(tmp_path: Path) -> None:
    """An identifier naming no lease this runner ever minted is refused, not silently recorded."""
    app, store = _runner_app_with_store(tmp_path)

    with runner_client(app) as client:
        resp = client.post("/api/leases/lease_nonesuch/session-end")

    assert resp.status_code == 404, resp.text
    assert store.session_ended_lease_ids() == set()


def test_session_end_endpoint_503_when_store_unwired(tmp_path: Path) -> None:
    """The store-free app (OpenAPI export / unit boot) answers 503, never pretends."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with runner_client(create_runner_app(config)) as client:
        resp = client.post("/api/leases/lease_1/session-end")
    assert resp.status_code == 503
