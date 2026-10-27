"""A runner set to release gives a gate-parked chunk back to the queue (service tier): one host, two runners serving
one project, each spending its own sampled subscription. A runner whose gate declines a chunk's next step releases it,
and a neighbour whose gate admits claims it; while every runner declines, the chunk stays ``ready``, and its first
runner claims it again once its own gate reopens. A usage-limited lease on a runner set to release is let go of with
its usage booked, and claimed elsewhere from its step's start. Samples are written into the host store directly, as
the service tier has no fake sampler. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from blizzard.runner.throttle.settings import HoldOrRelease, ThrottleSettings, UtilizationSettings
from tests.fleet_launchers import REPO
from tests.service.runner_support import (
    Added,
    add_host,
    add_project,
    add_runner,
    asserted_chunk_status,
    host_leased,
    host_rows,
    limited_spec,
    mock_fleet_world,
    seed_project_chunk,
    seed_usage_sample,
    two_node_spec,
    two_plan_host,
)
from tests.service.support import mock_hub, poll_until, service_gate
from tests.support import free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_FIRST, _SECOND = "anthropic", "second-plan"
#: The line both runners' utilization levers hold, and a reading on each side of it.
_LINE = 95.0
_UNDER, _OVER = 40.0, 96.0
_RELEASING_LINE = ThrottleSettings(utilization=UtilizationSettings(below_pct=_LINE), on_decline=HoldOrRelease.RELEASE)


def _plans(root: Path) -> tuple[SubscriptionDeclaration, ...]:
    no_credentials = str(root / "no-such-credentials.json")
    return tuple(
        SubscriptionDeclaration(slug=slug, name=slug, provider=PROVIDER_ANTHROPIC, credentials_path=no_credentials)
        for slug in (_FIRST, _SECOND)
    )


def _leases(root: Path, runner: Added, chunk_id: str) -> int:
    query = "SELECT COUNT(*) FROM leases WHERE runner_id = ? AND chunk_id = ?"
    return int(host_rows(root, query, runner.runner_id, chunk_id)[0][0])


def _holds_nothing(root: Path, runner: Added, chunk_id: str) -> bool:
    """``runner`` keeps no open lease, park, or environment for ``chunk_id``."""
    open_leases = host_rows(
        root,
        "SELECT COUNT(*) FROM leases l WHERE l.runner_id = ? AND l.chunk_id = ?"
        " AND NOT EXISTS (SELECT 1 FROM lease_closures c WHERE c.lease_id = l.lease_id)",
        runner.runner_id,
        chunk_id,
    )[0][0]
    open_parks = host_rows(
        root,
        "SELECT COUNT(*) FROM pause_parks p WHERE p.runner_id = ? AND p.chunk_id = ?"
        " AND NOT EXISTS (SELECT 1 FROM lease_closures c WHERE c.lease_id = p.lease_id)"
        " AND NOT EXISTS (SELECT 1 FROM pause_park_resumes r WHERE r.lease_id = p.lease_id)",
        runner.runner_id,
        chunk_id,
    )[0][0]
    held = host_rows(
        root,
        "SELECT COUNT(*) FROM env_bindings b WHERE b.runner_id = ? AND b.chunk_id = ?"
        " AND NOT EXISTS (SELECT 1 FROM binding_releases r WHERE r.chunk_id = b.chunk_id"
        " AND r.environment_id = b.environment_id AND r.released_at >= b.bound_at)",
        runner.runner_id,
        chunk_id,
    )[0][0]
    return (open_leases, open_parks, held) == (0, 0, 0)


def _ready(hub: httpx2.Client, chunk_id: str) -> bool:
    return asserted_chunk_status(hub, chunk_id) == "ready"


def test_a_declined_next_step_is_released_to_a_neighbour_and_waits_ready_while_every_gate_declines(
    tmp_path: Path,
) -> None:
    bin_dir, workspace = mock_fleet_world(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    root.mkdir()
    reset = datetime.now(UTC) + timedelta(hours=3)
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = add_host(hub)
        project = add_project(hub, "p-a")
        first = add_runner(hub, "runner-a", "p-a", host_id=host_id)
        neighbour = add_runner(hub, "runner-b", "p-a", host_id=host_id)
        log = root / "daemon.log"
        spenders = ((first, _FIRST), (neighbour, _SECOND))
        with two_plan_host(root, bin_dir, hub_port, workspace, host_token, spenders, _plans(root), _RELEASING_LINE):
            # Only the first runner admits a claim.
            seed_usage_sample(root, _FIRST, utilization_pct=_UNDER, resets_at=reset)
            seed_usage_sample(root, _SECOND, utilization_pct=_OVER, resets_at=reset)
            chunk = seed_project_chunk(hub, project, two_node_spec(f"{REPO}/issues/1"))
            assert poll_until(lambda: host_leased(root, first.runner_id, chunk), timeout=60.0), read_daemon_log(log)

            # The line is crossed while the first step runs: it finishes, and the runner gives the chunk back.
            seed_usage_sample(root, _FIRST, utilization_pct=_OVER, resets_at=reset)
            assert poll_until(lambda: _ready(hub, chunk) and _holds_nothing(root, first, chunk), timeout=60.0), (
                read_daemon_log(log)
            )
            assert _leases(root, first, chunk) == 1

            # Every gate declines: the chunk waits ready, claimed by no one.
            assert not poll_until(lambda: not _ready(hub, chunk), timeout=6.0), read_daemon_log(log)
            assert (_leases(root, first, chunk), _leases(root, neighbour, chunk)) == (1, 0)

            # A neighbour whose gate admits claims it.
            seed_usage_sample(root, _SECOND, utilization_pct=_UNDER, resets_at=reset)
            assert poll_until(lambda: host_leased(root, neighbour.runner_id, chunk), timeout=60.0), read_daemon_log(log)
            assert _leases(root, first, chunk) == 1


def test_a_released_chunk_is_claimed_cold_by_its_first_runner_once_its_gate_reopens(tmp_path: Path) -> None:
    bin_dir, workspace = mock_fleet_world(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    root.mkdir()
    reset = datetime.now(UTC) + timedelta(hours=3)
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = add_host(hub)
        project = add_project(hub, "p-a")
        first = add_runner(hub, "runner-a", "p-a", host_id=host_id)
        neighbour = add_runner(hub, "runner-b", "p-a", host_id=host_id)
        log = root / "daemon.log"
        spenders = ((first, _FIRST), (neighbour, _SECOND))
        with two_plan_host(root, bin_dir, hub_port, workspace, host_token, spenders, _plans(root), _RELEASING_LINE):
            seed_usage_sample(root, _FIRST, utilization_pct=_UNDER, resets_at=reset)
            seed_usage_sample(root, _SECOND, utilization_pct=_OVER, resets_at=reset)
            chunk = seed_project_chunk(hub, project, two_node_spec(f"{REPO}/issues/1"))
            assert poll_until(lambda: host_leased(root, first.runner_id, chunk), timeout=60.0), read_daemon_log(log)
            seed_usage_sample(root, _FIRST, utilization_pct=_OVER, resets_at=reset)
            assert poll_until(lambda: _ready(hub, chunk) and _holds_nothing(root, first, chunk), timeout=60.0), (
                read_daemon_log(log)
            )

            # The first runner's gate reopens: it claims the chunk afresh, with a new lease.
            seed_usage_sample(root, _FIRST, utilization_pct=_UNDER, resets_at=reset)
            assert poll_until(lambda: _leases(root, first, chunk) == 2, timeout=60.0), read_daemon_log(log)
            assert _leases(root, neighbour, chunk) == 0


def test_a_usage_limited_lease_on_a_runner_set_to_release_is_claimed_elsewhere_from_its_step_s_start(
    tmp_path: Path,
) -> None:
    bin_dir, workspace = mock_fleet_world(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    root.mkdir()
    reset = (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0)
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = add_host(hub)
        project = add_project(hub, "p-a")
        first = add_runner(hub, "runner-a", "p-a", host_id=host_id)
        neighbour = add_runner(hub, "runner-b", "p-a", host_id=host_id)
        log = root / "daemon.log"
        spenders = ((first, _FIRST), (neighbour, _SECOND))
        throttles = {
            first.name: ThrottleSettings(on_usage_limit=HoldOrRelease.RELEASE),
            neighbour.name: ThrottleSettings(utilization=UtilizationSettings(below_pct=_LINE)),
        }
        with two_plan_host(root, bin_dir, hub_port, workspace, host_token, spenders, _plans(root), throttles=throttles):
            # Only the first runner admits a claim; its step exits usage-limited.
            seed_usage_sample(root, _FIRST, utilization_pct=90.0, resets_at=reset)
            seed_usage_sample(root, _SECOND, utilization_pct=_OVER, resets_at=reset)
            chunk = seed_project_chunk(hub, project, limited_spec(f"{REPO}/issues/1", reset))
            assert poll_until(lambda: host_leased(root, first.runner_id, chunk), timeout=60.0), read_daemon_log(log)

            # Its floor holds: the runner books the half-done step's usage and gives the chunk back.
            assert poll_until(lambda: _ready(hub, chunk) and _holds_nothing(root, first, chunk), timeout=90.0), (
                read_daemon_log(log)
            )
            [(lease_id,)] = host_rows(
                root, "SELECT lease_id FROM leases WHERE runner_id = ? AND chunk_id = ?", first.runner_id, chunk
            )
            assert host_rows(root, "SELECT reason FROM lease_closures WHERE lease_id = ?", lease_id) == [("released",)]
            booked = host_rows(
                root, "SELECT COUNT(*) FROM usage_facts WHERE lease_id = ? AND kind IN ('spawn', 'resume')", lease_id
            )
            assert booked == [(1,)], read_daemon_log(log)

            # A neighbour whose gate admits claims it, entering the step from its start under its own lease.
            seed_usage_sample(root, _SECOND, utilization_pct=_UNDER, resets_at=reset)
            assert poll_until(lambda: host_leased(root, neighbour.runner_id, chunk), timeout=60.0), read_daemon_log(log)
            assert host_rows(
                root,
                "SELECT node_name FROM lease_context WHERE runner_id = ? AND chunk_id = ?",
                neighbour.runner_id,
                chunk,
            ) == [("build",)]
            assert _leases(root, first, chunk) == 1
            assert _holds_nothing(root, first, chunk)
