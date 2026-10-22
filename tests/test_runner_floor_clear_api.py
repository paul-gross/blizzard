"""The floor-clear routes (component tier): a subscription's floor cleared on the host by slug, a runner's own cleared
under its prefix — ``404`` for a slug the host does not declare, ``409`` naming the slug for a runner that spends
one — each clear recorded with who made it and read back as the newest."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from blizzard.runner.throttle.usage_limits import FloorClear
from tests.config_inputs import create_runner_app
from tests.runner_fakes import SqlAlchemyRunnerStore, make_host_stores, make_store, make_stores

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)
_PLAN = SubscriptionDeclaration(slug="team-plan", name="Team plan", provider=PROVIDER_ANTHROPIC)


def _client(tmp_path: Path, *, spends: bool) -> tuple[TestClient, SqlAlchemyRunnerStore, RunnerConfig]:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(
        root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", subscriptions=(_PLAN,) if spends else ()
    )
    clock = FixedClock(_NOW)
    app = create_runner_app(
        config,
        runner_stores=make_stores(store),
        host_stores=make_host_stores(store, clock),
        subscriptions=(_PLAN,),
        clock=clock,
    )
    return TestClient(app), store, config


def test_a_subscription_clear_records_who_cleared_it(tmp_path: Path) -> None:
    client, store, _config = _client(tmp_path, spends=True)

    resp = client.post("/api/host/subscriptions/team-plan/floor/clear", json={"by": "alice"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"slug": "team-plan", "cleared_at": "2026-10-22T12:00:00+00:00", "by": "alice"}
    assert make_host_stores(store).usage_limits.latest_floor_clear("team-plan") == FloorClear(_NOW, "alice")


def test_a_subscription_clear_defaults_its_author_to_the_operator(tmp_path: Path) -> None:
    client, _store, _config = _client(tmp_path, spends=True)

    resp = client.post("/api/host/subscriptions/team-plan/floor/clear", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["by"] == "operator"


def test_an_undeclared_subscription_is_not_found(tmp_path: Path) -> None:
    client, store, _config = _client(tmp_path, spends=True)

    resp = client.post("/api/host/subscriptions/nobody/floor/clear", json={})

    assert resp.status_code == 404, resp.text
    assert make_host_stores(store).usage_limits.latest_floor_clear("nobody") is None


def test_a_runner_spending_none_clears_its_own_floor(tmp_path: Path) -> None:
    client, store, config = _client(tmp_path, spends=False)

    resp = client.post(f"/runners/{config.served_id()}/api/runner/floor/clear", json={"by": "bob"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"slug": None, "cleared_at": "2026-10-22T12:00:00+00:00", "by": "bob"}
    assert store.latest_floor_clear() == FloorClear(_NOW, "bob")


def test_a_runner_spending_a_subscription_refuses_its_own_clear_naming_the_slug(tmp_path: Path) -> None:
    client, store, config = _client(tmp_path, spends=True)

    resp = client.post(f"/runners/{config.served_id()}/api/runner/floor/clear", json={})

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["slug"] == "team-plan"
    assert store.latest_floor_clear() is None
    assert make_host_stores(store).usage_limits.latest_floor_clear("team-plan") is None
