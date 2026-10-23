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
from tests.service.support import (
    BUILD_SCRIPT,
    JUDGEMENT_SCRIPT,
    mock_hub,
    mock_hub_chunk_spec,
    poll_until,
    service_gate,
)
from tests.service.test_host_halted_runner_service import _add, _add_host, _add_project, _fixture, _leased, _rows
from tests.service.test_subscription_floor_service import (
    _declining,
    _sample,
    _seed,
    _two_plan_host,
)
from tests.support import free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

#: How long the first node holds its step open, so a sample can land while it runs.
_STEP_SECONDS = 8


def _two_node_spec(work_ref: str) -> dict:
    """The scripted build chunk with a slow first node and a runner-executed ``review`` after it."""
    spec = mock_hub_chunk_spec(work_ref)
    build = spec["nodes"]["build"]
    build["prompt"] = f"import time\ntime.sleep({_STEP_SECONDS})\n{BUILD_SCRIPT}"
    build["choices"] = [{"name": "pass", "description": "committed and green", "to": "review"}]
    spec["nodes"]["review"] = {
        "executor": "runner",
        "session": "resume",
        "judged_by": "worker",
        "prompt": "pass\n",
        "judgement_prompt": JUDGEMENT_SCRIPT,
        "choices": [{"name": "pass", "description": "ready to land", "to": "deliver"}],
        "retries_max": 1,
    }
    return spec


def _node_leases(root: Path, chunk_id: str, node: str) -> int:
    query = "SELECT COUNT(*) FROM lease_context WHERE chunk_id = ? AND node_name = ?"
    return int(_rows(root, query, chunk_id, node)[0][0])


def test_a_utilization_line_declines_the_next_node_and_never_the_step_in_flight(tmp_path: Path) -> None:
    bin_dir, workspace = _fixture(tmp_path)
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
        host_id, host_token = _add_host(hub)
        project = _add_project(hub, "p-a")
        runner = _add(hub, "runner-a", "p-a", host_id=host_id)
        log = root / "daemon.log"
        line = ThrottleSettings(utilization=UtilizationSettings(below_pct=95.0))
        with _two_plan_host(root, bin_dir, hub_port, workspace, host_token, ((runner, "anthropic"),), plans, line):
            _sample(root, "anthropic", utilization_pct=40.0, resets_at=reset)
            chunk = _seed(hub, project, _two_node_spec(f"{REPO}/issues/1"))
            assert poll_until(lambda: _leased(root, runner.runner_id, chunk), timeout=60.0), read_daemon_log(log)

            # The line is crossed while the first step runs: it finishes, and its next node is declined.
            _sample(root, "anthropic", utilization_pct=96.0, resets_at=reset)
            assert poll_until(lambda: _declining(hub, runner) == ["utilization"], timeout=30.0), read_daemon_log(log)
            assert _node_leases(root, chunk, "build") == 1
            assert _stays_unleased_node(root, chunk, "review", for_seconds=10.0), read_daemon_log(log)
            assert _node_leases(root, chunk, "build") == 1, "the declined node spent a retry"

            # A sample under the line: the next node enters, and the hub shows nothing declining.
            _sample(root, "anthropic", utilization_pct=40.0, resets_at=reset)
            assert poll_until(lambda: _node_leases(root, chunk, "review") == 1, timeout=60.0), read_daemon_log(log)
            assert poll_until(lambda: _declining(hub, runner) == [], timeout=30.0), read_daemon_log(log)
            assert _node_leases(root, chunk, "build") == 1


def _stays_unleased_node(root: Path, chunk_id: str, node: str, *, for_seconds: float) -> bool:
    """``node`` of ``chunk_id`` never gets a lease in ``for_seconds`` — several ticks."""
    return not poll_until(lambda: _node_leases(root, chunk_id, node) > 0, timeout=for_seconds)
