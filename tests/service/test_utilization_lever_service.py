"""A utilization line holds a runner's next node and not the step in flight (service tier): one host, one runner
spending a sampled subscription and configuring a line. A sample over the line stands while the chunk's first node
runs; that step finishes, its next node is declined — no lease minted, no retry spent — and the hub shows the runner
declining on ``utilization``; a sample under the line lets the next node enter. Samples are written into the host
store directly, as the service tier has no fake sampler. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from blizzard.runner.throttle.settings import ThrottleSettings, UtilizationSettings
from tests.fleet_launchers import REPO
from tests.service.runner_support import (
    add_host,
    add_project,
    add_runner,
    declining_levers,
    host_leased,
    mock_fleet_world,
    node_leases,
    seed_project_chunk,
    seed_usage_sample,
    stays_unleased_node,
    two_node_spec,
    two_plan_host,
)
from tests.service.support import (
    mock_hub,
    poll_until,
    service_gate,
)
from tests.support import free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]


def test_a_utilization_line_declines_the_next_node_and_never_the_step_in_flight(tmp_path: Path) -> None:
    bin_dir, workspace = mock_fleet_world(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    root.mkdir()
    plans = (
        SubscriptionDeclaration(
            slug="anthropic",
            name="Anthropic",
            provider=PROVIDER_ANTHROPIC,
            credentials_path=str(root / "no-such-credentials.json"),
        ),
    )
    reset = datetime.now(UTC) + timedelta(hours=3)
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = add_host(hub)
        project = add_project(hub, "p-a")
        runner = add_runner(hub, "runner-a", "p-a", host_id=host_id)
        log = root / "daemon.log"
        line = ThrottleSettings(utilization=UtilizationSettings(below_pct=95.0))
        with two_plan_host(root, bin_dir, hub_port, workspace, host_token, ((runner, "anthropic"),), plans, line):
            seed_usage_sample(root, "anthropic", utilization_pct=40.0, resets_at=reset)
            chunk = seed_project_chunk(hub, project, two_node_spec(f"{REPO}/issues/1"))
            assert poll_until(lambda: host_leased(root, runner.runner_id, chunk), timeout=60.0), read_daemon_log(log)

            # The line is crossed while the first step runs: it finishes, and its next node is declined.
            seed_usage_sample(root, "anthropic", utilization_pct=96.0, resets_at=reset)
            assert poll_until(lambda: declining_levers(hub, runner) == ["utilization"], timeout=30.0), read_daemon_log(
                log
            )
            assert node_leases(root, chunk, "build") == 1
            assert stays_unleased_node(root, chunk, "review", for_seconds=10.0), read_daemon_log(log)
            assert node_leases(root, chunk, "build") == 1, "the declined node spent a retry"

            # A sample under the line: the next node enters, and the hub shows nothing declining.
            seed_usage_sample(root, "anthropic", utilization_pct=40.0, resets_at=reset)
            assert poll_until(lambda: node_leases(root, chunk, "review") == 1, timeout=60.0), read_daemon_log(log)
            assert poll_until(lambda: declining_levers(hub, runner) == [], timeout=30.0), read_daemon_log(log)
            assert node_leases(root, chunk, "build") == 1
