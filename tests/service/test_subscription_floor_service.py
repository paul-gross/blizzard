"""A usage limit holds every runner spending its subscription, and no other (service tier): one host, two
subscriptions, two runners spending the first and one the second. No runner's own brake engages; a turned-over sample
opens the floor unaided, and so does ``clear-floor`` with a sample still showing it shut; the hub shows the floor as
each held runner's declining reason while it holds, and nothing once it opens. Samples are written into the
host store directly, as the service tier has no fake sampler. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from tests.fleet_launchers import REPO
from tests.runner_join import RUNNER_BIN, runner_spawn_env
from tests.service.runner_support import (
    USAGE_WINDOW,
    Added,
    add_host,
    add_project,
    add_runner,
    declining_levers,
    host_leased,
    host_rows,
    limited_spec,
    mock_fleet_world,
    seed_project_chunk,
    seed_usage_sample,
    two_plan_host,
)
from tests.service.support import mock_hub, poll_until, service_gate
from tests.support import free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_SECOND = "second-plan"


def _marks(root: Path, slug: str) -> list[tuple[str, str | None]]:
    """Each mark on ``slug``, oldest first: the lease it parked and the window it holds."""
    return [
        (str(lease), window)
        for lease, window in host_rows(
            root, "SELECT lease_id, window FROM subscription_usage_limits WHERE slug = ? ORDER BY id", slug
        )
    ]


def _parked(root: Path, lease_id: str) -> bool:
    """``lease_id`` stands in a park it has not woken from."""
    parks = host_rows(root, "SELECT COUNT(*) FROM pause_parks WHERE lease_id = ?", lease_id)[0][0]
    resumes = host_rows(root, "SELECT COUNT(*) FROM pause_park_resumes WHERE lease_id = ?", lease_id)[0][0]
    return bool(parks > resumes)


def _woke(root: Path, lease_id: str) -> bool:
    return bool(host_rows(root, "SELECT 1 FROM pause_park_resumes WHERE lease_id = ?", lease_id))


def _assert_unbraked(local: httpx2.Client, runners: tuple[Added, ...]) -> None:
    for runner in runners:
        view = local.get(f"/runners/{runner.runner_id}/api/runner")
        assert view.status_code == 200, view.text
        assert view.json()["pause"]["local"] is False, (runner.name, view.json()["pause"])


def _stays_unleased(root: Path, runner: Added, chunk_id: str, *, for_seconds: float) -> bool:
    """``runner`` never leases ``chunk_id`` in ``for_seconds`` — several of its ticks."""
    return not poll_until(lambda: host_leased(root, runner.runner_id, chunk_id), timeout=for_seconds)


def test_a_usage_limit_holds_its_subscription_s_runners_until_a_sample_or_a_clear_opens_it(tmp_path: Path) -> None:
    bin_dir, workspace = mock_fleet_world(tmp_path)
    hub_port = free_port()
    root = tmp_path / "host"
    root.mkdir()
    no_credentials = str(root / "no-such-credentials.json")
    plans = (
        SubscriptionDeclaration(
            slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC, credentials_path=no_credentials
        ),
        SubscriptionDeclaration(
            slug=_SECOND, name="Second plan", provider=PROVIDER_ANTHROPIC, credentials_path=no_credentials
        ),
    )
    first = plans[0].slug
    # The reset the harness names, and the later period the window turns over into.
    reset = (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0)
    next_reset = reset + timedelta(hours=5)
    with mock_hub(bin_dir, hub_port) as hub:
        host_id, host_token = add_host(hub)
        project_a, project_b, project_c = (add_project(hub, slug) for slug in ("p-a", "p-b", "p-c"))
        runner_a = add_runner(hub, "runner-a", "p-a", host_id=host_id)
        runner_b = add_runner(hub, "runner-b", "p-b", host_id=host_id)
        runner_c = add_runner(hub, "runner-c", "p-c", host_id=host_id)
        everyone = (runner_a, runner_b, runner_c)
        spenders = ((runner_a, first), (runner_b, first), (runner_c, _SECOND))
        log = root / "daemon.log"
        with two_plan_host(root, bin_dir, hub_port, workspace, host_token, spenders, plans) as local:
            # Before the limit: a sample placing its window, whose reset is the one the harness names.
            seed_usage_sample(root, first, utilization_pct=90.0, resets_at=reset)
            seed_project_chunk(hub, project_a, limited_spec(f"{REPO}/issues/1", reset))

            assert poll_until(lambda: len(_marks(root, first)) == 1, timeout=90.0), read_daemon_log(log)
            [(limited_lease, held)] = _marks(root, first)
            assert held == USAGE_WINDOW, "the mark placed no held window"
            assert poll_until(lambda: _parked(root, limited_lease), timeout=30.0), read_daemon_log(log)

            # The limit holds both of its subscription's runners, and not the third.
            held_a, held_b = seed_project_chunk(hub, project_a), seed_project_chunk(hub, project_b)
            free_c = seed_project_chunk(hub, project_c)
            assert poll_until(lambda: host_leased(root, runner_c.runner_id, free_c), timeout=60.0), read_daemon_log(log)
            assert _stays_unleased(root, runner_b, held_b, for_seconds=5.0), read_daemon_log(log)
            assert not host_leased(root, runner_a.runner_id, held_a)
            assert _parked(root, limited_lease)
            _assert_unbraked(local, everyone)
            # The hub shows the floor as the reason for the two it holds, and nothing for the third.
            assert poll_until(
                lambda: declining_levers(hub, runner_a) == ["floor"] and declining_levers(hub, runner_b) == ["floor"],
                timeout=30.0,
            ), read_daemon_log(log)
            assert declining_levers(hub, runner_c) == []

            # The window turned over, under 100 percent: the floor opens before its reset, with nobody acting.
            seed_usage_sample(root, first, utilization_pct=20.0, resets_at=next_reset)
            free_c = seed_project_chunk(hub, project_c)
            assert poll_until(
                lambda: (
                    _woke(root, limited_lease)
                    and host_leased(root, runner_a.runner_id, held_a)
                    and host_leased(root, runner_b.runner_id, held_b)
                    and host_leased(root, runner_c.runner_id, free_c)
                ),
                timeout=90.0,
            ), read_daemon_log(log)
            _assert_unbraked(local, everyone)
            assert poll_until(lambda: declining_levers(hub, runner_a) == [], timeout=30.0), read_daemon_log(log)

            # A second limit, on the other spender, in the new period.
            limited_again = seed_project_chunk(hub, project_b, limited_spec(f"{REPO}/issues/2", next_reset))
            assert poll_until(lambda: len(_marks(root, first)) == 2, timeout=90.0), read_daemon_log(log)
            relimited_lease, held = _marks(root, first)[1]
            assert held == USAGE_WINDOW
            assert host_leased(root, runner_b.runner_id, limited_again)
            assert poll_until(lambda: _parked(root, relimited_lease), timeout=30.0), read_daemon_log(log)
            # A sample still showing the window shut in its current period.
            seed_usage_sample(root, first, utilization_pct=100.0, resets_at=next_reset)
            held_a, held_b = seed_project_chunk(hub, project_a), seed_project_chunk(hub, project_b)
            free_c = seed_project_chunk(hub, project_c)
            assert poll_until(lambda: host_leased(root, runner_c.runner_id, free_c), timeout=60.0), read_daemon_log(log)
            assert _stays_unleased(root, runner_a, held_a, for_seconds=5.0), read_daemon_log(log)
            assert not host_leased(root, runner_b.runner_id, held_b)
            _assert_unbraked(local, everyone)
            assert poll_until(lambda: declining_levers(hub, runner_a) == ["floor"], timeout=30.0), read_daemon_log(log)

            cleared = subprocess.run(
                [str(RUNNER_BIN), "clear-floor", "--subscription", first, "--dir", str(root)],
                capture_output=True,
                text=True,
                env=runner_spawn_env(f"http://127.0.0.1:{hub_port}"),
                timeout=60,
                check=False,
            )
            assert cleared.returncode == 0, cleared.stdout + cleared.stderr
            free_c = seed_project_chunk(hub, project_c)
            assert poll_until(
                lambda: (
                    _woke(root, relimited_lease)
                    and host_leased(root, runner_a.runner_id, held_a)
                    and host_leased(root, runner_b.runner_id, held_b)
                    and host_leased(root, runner_c.runner_id, free_c)
                ),
                timeout=90.0,
            ), read_daemon_log(log)
            assert host_rows(root, "SELECT by FROM subscription_floor_clears WHERE slug = ?", first) == [("operator",)]
            _assert_unbraked(local, everyone)
            assert poll_until(lambda: declining_levers(hub, runner_a) == [], timeout=30.0), read_daemon_log(log)
