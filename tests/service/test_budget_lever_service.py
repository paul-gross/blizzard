"""A dollar budget throttles a runner below its ceiling (service tier): one host, one runner spending a sampled
subscription and configuring a budget. A runner whose spend stands at its budget is declined — the hub shows it
declining on ``budget`` and no lease is minted — and carries on as that spend ages out of the trailing window; a
runner that spends through its budget to its ceiling trips the ceiling's local pause, which stays on after the spend
has aged out. Spend is written into the runner's usage facts directly, as the service tier has no priced harness. Run
with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.store.schema import usage_facts
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from blizzard.runner.throttle.settings import BudgetSettings, ThrottleSettings
from tests.fleet_launchers import REPO
from tests.service.support import mock_hub, mock_hub_chunk_spec, poll_until, service_gate
from tests.service.test_host_halted_runner_service import _add, _add_host, _add_project, _Added, _fixture, _leased
from tests.service.test_subscription_floor_service import _declining, _sample, _seed, _two_plan_host
from tests.service.test_utilization_lever_service import _node_leases, _stays_unleased_node, _two_node_spec
from tests.support import free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

#: The trailing window both the budget and the ceiling read, in hours — a minute and a half, so spend ages out inside
#: a test.
_WINDOW_HOURS = 0.025
_BUDGET_USD = 10.0
_CEILING_USD = 50.0


def _spend(root: Path, runner_id: str, usd: float) -> None:
    """``usd`` of billed usage recorded against ``runner_id`` just now."""
    engine = create_engine_from_url(f"sqlite:///{root / 'data' / 'host.db'}")
    try:
        with engine.begin() as conn:
            conn.execute(
                usage_facts.insert().values(
                    lease_id="lease_seeded",
                    chunk_id="ch_seeded",
                    node_id="nd_seeded",
                    epoch=1,
                    generation=1,
                    kind="spawn",
                    model="seeded",
                    input_tokens=0,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_create_tokens=0,
                    cost_usd=usd,
                    recorded_at=datetime.now(UTC),
                    runner_id=runner_id,
                )
            )
    finally:
        engine.dispose()


def _locally_paused(local: httpx2.Client, runner_id: str) -> bool:
    view = local.get(f"/runners/{runner_id}/api/runner")
    assert view.status_code == 200, view.text
    return bool(view.json()["pause"]["local"])


def _declining_once_registered(hub: httpx2.Client, runner: _Added) -> list[str] | None:
    """The levers the hub holds ``runner`` declining on, or ``None`` while it has not yet registered."""
    view = hub.get(f"/api/fleet/runners/{runner.runner_id}", headers={"Authorization": f"Bearer {runner.token}"})
    return _declining(hub, runner) if view.status_code == 200 else None


def _plans(root: Path) -> tuple[SubscriptionDeclaration, ...]:
    return (
        SubscriptionDeclaration(
            slug="anthropic",
            name="Anthropic",
            provider=PROVIDER_ANTHROPIC,
            credentials_path=str(root / "no-such-credentials.json"),
        ),
    )


def test_a_runner_at_its_budget_waits_for_its_spend_to_age_out_then_carries_on(tmp_path: Path) -> None:
    bin_dir, workspace = _fixture(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    root.mkdir()
    budget = ThrottleSettings(budget=BudgetSettings(usd=_BUDGET_USD, window_hours=_WINDOW_HOURS))
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = _add_host(hub)
        project = _add_project(hub, "p-a")
        runner = _add(hub, "runner-a", "p-a", host_id=host_id)
        log = root / "daemon.log"
        with _two_plan_host(
            root, bin_dir, hub_port, workspace, host_token, ((runner, "anthropic"),), _plans(root), budget
        ):
            _sample(root, "anthropic", utilization_pct=10.0, resets_at=datetime.now(UTC) + timedelta(hours=3))
            _spend(root, runner.runner_id, _BUDGET_USD + 2.0)
            chunk = _seed(hub, project, mock_hub_chunk_spec(f"{REPO}/issues/1"))

            # The spend stands inside the window: the runner is declined on the budget and mints no lease.
            assert poll_until(lambda: _declining_once_registered(hub, runner) == ["budget"], timeout=30.0), (
                read_daemon_log(log)
            )
            assert not _leased(root, runner.runner_id, chunk), "a runner at its budget claimed"

            # The spend ages out of the window: the budget admits again and the runner carries on.
            assert poll_until(lambda: _leased(root, runner.runner_id, chunk), timeout=120.0), read_daemon_log(log)
            assert poll_until(lambda: _declining_once_registered(hub, runner) == [], timeout=30.0), read_daemon_log(log)


def test_a_runner_spending_through_its_budget_trips_its_ceiling_and_stays_tripped(tmp_path: Path) -> None:
    bin_dir, workspace = _fixture(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    root.mkdir()
    budget = ThrottleSettings(budget=BudgetSettings(usd=_BUDGET_USD, window_hours=_WINDOW_HOURS))
    ceiling = {"runner_ceiling_usd": _CEILING_USD, "runner_ceiling_window_hours": _WINDOW_HOURS}
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = _add_host(hub)
        project = _add_project(hub, "p-a")
        runner = _add(hub, "runner-a", "p-a", host_id=host_id)
        log = root / "daemon.log"
        with _two_plan_host(
            root, bin_dir, hub_port, workspace, host_token, ((runner, "anthropic"),), _plans(root), budget, ceiling
        ) as local:
            _sample(root, "anthropic", utilization_pct=10.0, resets_at=datetime.now(UTC) + timedelta(hours=3))
            chunk = _seed(hub, project, _two_node_spec(f"{REPO}/issues/1"))
            assert poll_until(lambda: _leased(root, runner.runner_id, chunk), timeout=60.0), read_daemon_log(log)
            assert not _locally_paused(local, runner.runner_id)

            # The admitted step spends through the budget and on to the ceiling: the ceiling trips.
            _spend(root, runner.runner_id, _CEILING_USD + 1.0)
            assert poll_until(lambda: _locally_paused(local, runner.runner_id), timeout=30.0), read_daemon_log(log)

            # The spend ages out of the window, which would admit the budget again; the ceiling stays tripped.
            assert not poll_until(
                lambda: not _locally_paused(local, runner.runner_id), timeout=_WINDOW_HOURS * 3600 + 20
            )
            assert _stays_unleased_node(root, chunk, "review", for_seconds=5.0), read_daemon_log(log)
            assert _node_leases(root, chunk, "build") == 1
