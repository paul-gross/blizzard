"""The worker heartbeat path — local-API endpoint + ``blizzard runner heartbeat`` verb.

Two tiers, no live socket: **component** drives the endpoint over a real store
(TestClient); **unit** covers the verb's identity handling and soft-fail
(``httpx.post`` stubbed).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from blizzard.runner.app import create_app
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import NewLease
from tests.runner_fakes import make_store, make_stores

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _runner_app_with_store(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A runner app wired to a real (migrated) store — the ``host`` heartbeat surface."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    return create_app(config, runner_stores=make_stores(store)), store


def _seed_lease(store, lease_id: str = "lease_1") -> None:  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id=lease_id,
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


# The local-API endpoint (component tier)


@pytest.mark.component
def test_heartbeat_endpoint_records_a_beat(tmp_path: Path) -> None:
    app, store = _runner_app_with_store(tmp_path)
    _seed_lease(store)
    assert store.latest_heartbeat("lease_1") is None

    with TestClient(app) as client:
        resp = client.post("/api/heartbeat", json={"lease_id": "lease_1"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"recorded": True, "lease_id": "lease_1"}
    assert store.latest_heartbeat("lease_1") is not None  # REAP's stall signal now exists


@pytest.mark.component
def test_heartbeat_endpoint_records_for_an_already_closed_lease(tmp_path: Path) -> None:
    """The closure-spanning resolution keeps tolerating a beat racing a lease's own close."""
    app, store = _runner_app_with_store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)

    with TestClient(app) as client:
        resp = client.post("/api/heartbeat", json={"lease_id": "lease_1"})

    assert resp.status_code == 200, resp.text
    assert store.latest_heartbeat("lease_1") is not None


@pytest.mark.component
def test_heartbeat_endpoint_404_for_an_unminted_lease(tmp_path: Path) -> None:
    """An identifier naming no lease this runner ever minted is refused, not silently recorded."""
    app, store = _runner_app_with_store(tmp_path)

    with TestClient(app) as client:
        resp = client.post("/api/heartbeat", json={"lease_id": "lease_nonesuch"})

    assert resp.status_code == 404, resp.text
    assert store.latest_heartbeat("lease_nonesuch") is None


@pytest.mark.component
def test_heartbeat_endpoint_503_when_store_unwired(tmp_path: Path) -> None:
    """The store-free app (OpenAPI export / unit boot) answers 503, never pretends."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        resp = client.post("/api/heartbeat", json={"lease_id": "lease_1"})
    assert resp.status_code == 503


# The `blizzard runner heartbeat` verb (unit tier)


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


def test_heartbeat_verb_posts_inherited_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verb reads the spawn-injected identity and posts it — no arguments."""
    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, *, json: dict, timeout: float, **_: object) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["heartbeat"],
        env={"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:8431/"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://127.0.0.1:8431/api/heartbeat", {"lease_id": "lease_9"})]


def test_heartbeat_verb_soft_fails_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hook must never break the worker's tool call — no identity means a clean skip."""
    posted = False

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        nonlocal posted
        posted = True
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(runner_group, ["heartbeat"], env={"BLIZZARD_LEASE_ID": "", "BLIZZARD_RUNNER_URL": ""})

    assert result.exit_code == 0  # soft-fail, never raise
    assert "skipping" in result.output
    assert posted is False  # never even attempted the post


def test_heartbeat_verb_soft_fails_when_runner_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable runner is reported and swallowed — exit 0, tool call unbroken."""

    def fake_post(*args: object, **kwargs: object) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        runner_group,
        ["heartbeat"],
        env={"BLIZZARD_LEASE_ID": "lease_9", "BLIZZARD_RUNNER_URL": "http://127.0.0.1:1/"},
    )

    assert result.exit_code == 0
    assert "could not reach the runner" in result.output
