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
from tests.service.support import mock_hub, poll_until, service_gate
from tests.service.test_host_halted_runner_service import (
    _add,
    _add_host,
    _add_project,
    _Added,
    _chunk_status,
    _fixture,
    _leased,
    _rows,
)
from tests.service.test_subscription_floor_service import _limited_spec, _sample, _seed, _two_plan_host
from tests.service.test_utilization_lever_service import _two_node_spec
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


def _leases(root: Path, runner: _Added, chunk_id: str) -> int:
    query = "SELECT COUNT(*) FROM leases WHERE runner_id = ? AND chunk_id = ?"
    return int(_rows(root, query, runner.runner_id, chunk_id)[0][0])


def _holds_nothing(root: Path, runner: _Added, chunk_id: str) -> bool:
    """``runner`` keeps no open lease, park, or environment for ``chunk_id``."""
    open_leases = _rows(
        root,
        "SELECT COUNT(*) FROM leases l WHERE l.runner_id = ? AND l.chunk_id = ?"
        " AND NOT EXISTS (SELECT 1 FROM lease_closures c WHERE c.lease_id = l.lease_id)",
        runner.runner_id,
        chunk_id,
    )[0][0]
    open_parks = _rows(
        root,
        "SELECT COUNT(*) FROM pause_parks p WHERE p.runner_id = ? AND p.chunk_id = ?"
        " AND NOT EXISTS (SELECT 1 FROM lease_closures c WHERE c.lease_id = p.lease_id)"
        " AND NOT EXISTS (SELECT 1 FROM pause_park_resumes r WHERE r.lease_id = p.lease_id)",
        runner.runner_id,
        chunk_id,
    )[0][0]
    held = _rows(
        root,
        "SELECT COUNT(*) FROM env_bindings b WHERE b.runner_id = ? AND b.chunk_id = ?"
        " AND NOT EXISTS (SELECT 1 FROM binding_releases r WHERE r.chunk_id = b.chunk_id"
        " AND r.environment_id = b.environment_id AND r.released_at >= b.bound_at)",
        runner.runner_id,
        chunk_id,
    )[0][0]
    return (open_leases, open_parks, held) == (0, 0, 0)


def _ready(hub: httpx2.Client, chunk_id: str) -> bool:
    return _chunk_status(hub, chunk_id) == "ready"


def test_a_declined_next_step_is_released_to_a_neighbour_and_waits_ready_while_every_gate_declines(
    tmp_path: Path,
) -> None:
    bin_dir, workspace = _fixture(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    root.mkdir()
    reset = datetime.now(UTC) + timedelta(hours=3)
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = _add_host(hub)
        project = _add_project(hub, "p-a")
        first = _add(hub, "runner-a", "p-a", host_id=host_id)
        neighbour = _add(hub, "runner-b", "p-a", host_id=host_id)
        log = root / "daemon.log"
        spenders = ((first, _FIRST), (neighbour, _SECOND))
        with _two_plan_host(root, bin_dir, hub_port, workspace, host_token, spenders, _plans(root), _RELEASING_LINE):
            # Only the first runner admits a claim.
            _sample(root, _FIRST, utilization_pct=_UNDER, resets_at=reset)
            _sample(root, _SECOND, utilization_pct=_OVER, resets_at=reset)
            chunk = _seed(hub, project, _two_node_spec(f"{REPO}/issues/1"))
            assert poll_until(lambda: _leased(root, first.runner_id, chunk), timeout=60.0), read_daemon_log(log)

            # The line is crossed while the first step runs: it finishes, and the runner gives the chunk back.
            _sample(root, _FIRST, utilization_pct=_OVER, resets_at=reset)
            assert poll_until(lambda: _ready(hub, chunk) and _holds_nothing(root, first, chunk), timeout=60.0), (
                read_daemon_log(log)
            )
            assert _leases(root, first, chunk) == 1

            # Every gate declines: the chunk waits ready, claimed by no one.
            assert not poll_until(lambda: not _ready(hub, chunk), timeout=6.0), read_daemon_log(log)
            assert (_leases(root, first, chunk), _leases(root, neighbour, chunk)) == (1, 0)

            # A neighbour whose gate admits claims it.
            _sample(root, _SECOND, utilization_pct=_UNDER, resets_at=reset)
            assert poll_until(lambda: _leased(root, neighbour.runner_id, chunk), timeout=60.0), read_daemon_log(log)
            assert _leases(root, first, chunk) == 1


def test_a_released_chunk_is_claimed_cold_by_its_first_runner_once_its_gate_reopens(tmp_path: Path) -> None:
    bin_dir, workspace = _fixture(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    root.mkdir()
    reset = datetime.now(UTC) + timedelta(hours=3)
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = _add_host(hub)
        project = _add_project(hub, "p-a")
        first = _add(hub, "runner-a", "p-a", host_id=host_id)
        neighbour = _add(hub, "runner-b", "p-a", host_id=host_id)
        log = root / "daemon.log"
        spenders = ((first, _FIRST), (neighbour, _SECOND))
        with _two_plan_host(root, bin_dir, hub_port, workspace, host_token, spenders, _plans(root), _RELEASING_LINE):
            _sample(root, _FIRST, utilization_pct=_UNDER, resets_at=reset)
            _sample(root, _SECOND, utilization_pct=_OVER, resets_at=reset)
            chunk = _seed(hub, project, _two_node_spec(f"{REPO}/issues/1"))
            assert poll_until(lambda: _leased(root, first.runner_id, chunk), timeout=60.0), read_daemon_log(log)
            _sample(root, _FIRST, utilization_pct=_OVER, resets_at=reset)
            assert poll_until(lambda: _ready(hub, chunk) and _holds_nothing(root, first, chunk), timeout=60.0), (
                read_daemon_log(log)
            )

            # The first runner's gate reopens: it claims the chunk afresh, with a new lease.
            _sample(root, _FIRST, utilization_pct=_UNDER, resets_at=reset)
            assert poll_until(lambda: _leases(root, first, chunk) == 2, timeout=60.0), read_daemon_log(log)
            assert _leases(root, neighbour, chunk) == 0


def test_a_usage_limited_lease_on_a_runner_set_to_release_is_claimed_elsewhere_from_its_step_s_start(
    tmp_path: Path,
) -> None:
    bin_dir, workspace = _fixture(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    root.mkdir()
    reset = (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0)
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = _add_host(hub)
        project = _add_project(hub, "p-a")
        first = _add(hub, "runner-a", "p-a", host_id=host_id)
        neighbour = _add(hub, "runner-b", "p-a", host_id=host_id)
        log = root / "daemon.log"
        spenders = ((first, _FIRST), (neighbour, _SECOND))
        throttles = {
            first.name: ThrottleSettings(on_usage_limit=HoldOrRelease.RELEASE),
            neighbour.name: ThrottleSettings(utilization=UtilizationSettings(below_pct=_LINE)),
        }
        with _two_plan_host(
            root, bin_dir, hub_port, workspace, host_token, spenders, _plans(root), throttles=throttles
        ):
            # Only the first runner admits a claim; its step exits usage-limited.
            _sample(root, _FIRST, utilization_pct=90.0, resets_at=reset)
            _sample(root, _SECOND, utilization_pct=_OVER, resets_at=reset)
            chunk = _seed(hub, project, _limited_spec(f"{REPO}/issues/1", reset))
            assert poll_until(lambda: _leased(root, first.runner_id, chunk), timeout=60.0), read_daemon_log(log)

            # Its floor holds: the runner books the half-done step's usage and gives the chunk back.
            assert poll_until(lambda: _ready(hub, chunk) and _holds_nothing(root, first, chunk), timeout=90.0), (
                read_daemon_log(log)
            )
            [(lease_id,)] = _rows(
                root, "SELECT lease_id FROM leases WHERE runner_id = ? AND chunk_id = ?", first.runner_id, chunk
            )
            assert _rows(root, "SELECT reason FROM lease_closures WHERE lease_id = ?", lease_id) == [("released",)]
            booked = _rows(
                root, "SELECT COUNT(*) FROM usage_facts WHERE lease_id = ? AND kind IN ('spawn', 'resume')", lease_id
            )
            assert booked == [(1,)], read_daemon_log(log)

            # A neighbour whose gate admits claims it, entering the step from its start under its own lease.
            _sample(root, _SECOND, utilization_pct=_UNDER, resets_at=reset)
            assert poll_until(lambda: _leased(root, neighbour.runner_id, chunk), timeout=60.0), read_daemon_log(log)
            assert _rows(
                root,
                "SELECT node_name FROM lease_context WHERE runner_id = ? AND chunk_id = ?",
                neighbour.runner_id,
                chunk,
            ) == [("build",)]
            assert _leases(root, first, chunk) == 1
            assert _holds_nothing(root, first, chunk)
