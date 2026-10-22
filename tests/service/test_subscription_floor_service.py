"""A usage limit holds every runner spending its subscription, and no other (service tier): one host, two
subscriptions, two runners spending the first and one the second. No runner's own brake engages; a turned-over sample
opens the floor unaided, and so does ``clear-floor`` with a sample still showing it shut; the hub shows the floor as
each held runner's declining reason while it holds, and nothing once it opens. Samples are written into the
host store directly, as the service tier has no fake sampler. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE, hub_token_variable
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import ProjectBinding, SubscriptionDeclaration
from blizzard.runner.host_config import RunnerEntry
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.store.schema import external_usage_samples
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from tests.fleet_launchers import REPO, await_http, runner_config, terminate
from tests.host_files import host_config_of
from tests.runner_join import RUNNER_BIN, runner_spawn_env
from tests.service.support import mock_hub, mock_hub_chunk_spec, poll_until, service_gate
from tests.service.test_host_halted_runner_service import (
    _add,
    _add_host,
    _add_project,
    _Added,
    _fixture,
    _leased,
    _rows,
)
from tests.support import daemon_log_sink, free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_SECOND = "second-plan"
#: The window every seeded sample reports — the one the limit falls in.
_WINDOW = "five_hour"
#: Slots per runner: enough that a parked lease never starves the claims each leg asserts.
_AGENTS = 5


def _limited_spec(work_ref: str, resets_at: datetime) -> dict:
    """A chunk whose build exits usage-limited at once, naming ``resets_at``."""
    spec = mock_hub_chunk_spec(work_ref)
    named = resets_at.strftime("%-I:%M%p").lower()
    spec["nodes"]["build"]["prompt"] = f'usage_limited(resets_at="{named} (UTC)")\n'
    return spec


def _seed(hub: httpx.Client, project_id: str, spec: dict | None = None) -> str:
    seeded = hub.post(
        "/_seed/chunk", json={**(spec or mock_hub_chunk_spec(f"{REPO}/issues/1")), "project_id": project_id}
    )
    assert seeded.status_code == 201, seeded.text
    return str(seeded.json()["chunk_id"])


def _sample(root: Path, slug: str, *, utilization_pct: float, resets_at: datetime) -> None:
    """A successful sample of ``slug`` taken now, its one window at ``utilization_pct`` until ``resets_at``."""
    window = {
        "window": _WINDOW,
        "utilization_pct": utilization_pct,
        "resets_at": resets_at.isoformat(),
        "window_seconds": 5 * 3600,
    }
    engine = create_engine_from_url(f"sqlite:///{root / 'data' / 'host.db'}")
    try:
        with engine.begin() as conn:
            conn.execute(
                external_usage_samples.insert().values(
                    slug=slug, sampled_at=datetime.now(UTC), payload=json.dumps({"windows": [window]})
                )
            )
    finally:
        engine.dispose()


@contextlib.contextmanager
def _two_plan_host(
    root: Path,
    bin_dir: Path,
    hub_port: int,
    workspace: Path,
    host_token: str,
    runners: tuple[tuple[_Added, str], ...],
    plans: tuple[SubscriptionDeclaration, ...],
) -> Iterator[httpx.Client]:
    """``blizzard-runner host`` over ``root`` holding ``host_token`` and declaring ``plans``, one ``[[runner]]`` per
    ``runners`` entry spending the subscription it names, each bound to its own project at :data:`_AGENTS` slots;
    yields a client to its port once it answers ``/api/health``."""
    hub_url = f"http://127.0.0.1:{hub_port}"
    base = runner_config(root, workspace, bin_dir, hub_port, join=False)
    envs = tuple(f"e{i + 1}" for i in range(len(runners) * _AGENTS))
    base = dataclasses.replace(
        base,
        subscriptions=plans,
        workspaces=(dataclasses.replace(base.workspaces[0], envs=envs, max_environments=len(envs)),),
        # Where the mock harness writes, so the limit is read from the transcript.
        transcripts_root=str(workspace / ".blizzard-mock-harness" / "transcripts"),
    )
    workspace_id = base.workspaces[0].id
    entries = tuple(
        RunnerEntry(
            name=runner.name,
            id=runner.runner_id,
            subscription=slug,
            bindings=(ProjectBinding(runner.project, workspace_id, _AGENTS),),
            own=dataclasses.replace(base, name=runner.name, runner_id=runner.runner_id),
        )
        for runner, slug in runners
    )
    HostLayout(root).config_path.write_text(dataclasses.replace(host_config_of(base), runners=entries).to_toml())
    for runner, _slug in runners:
        HubTokenFile.of(root, hub_token_variable(runner.name)).write(runner.token)
    log = root / "daemon.log"
    proc = subprocess.Popen(
        [str(RUNNER_BIN), "host", "--dir", str(root)],
        env=runner_spawn_env(
            hub_url,
            {"BZ_RUNNER_TICK_SECONDS": "0.5", HOST_TOKEN_VARIABLE: host_token, "BLIZZARD_MOCK_HARNESS_FENCE": "1"},
        ),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{base.port}", timeout=10.0)
    try:
        await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        terminate(proc)


def _marks(root: Path, slug: str) -> list[tuple[str, str | None]]:
    """Each mark on ``slug``, oldest first: the lease it parked and the window it holds."""
    return [
        (str(lease), window)
        for lease, window in _rows(
            root, "SELECT lease_id, window FROM subscription_usage_limits WHERE slug = ? ORDER BY id", slug
        )
    ]


def _parked(root: Path, lease_id: str) -> bool:
    """``lease_id`` stands in a park it has not woken from."""
    parks = _rows(root, "SELECT COUNT(*) FROM pause_parks WHERE lease_id = ?", lease_id)[0][0]
    resumes = _rows(root, "SELECT COUNT(*) FROM pause_park_resumes WHERE lease_id = ?", lease_id)[0][0]
    return bool(parks > resumes)


def _woke(root: Path, lease_id: str) -> bool:
    return bool(_rows(root, "SELECT 1 FROM pause_park_resumes WHERE lease_id = ?", lease_id))


def _assert_unbraked(local: httpx.Client, runners: tuple[_Added, ...]) -> None:
    for runner in runners:
        view = local.get(f"/runners/{runner.runner_id}/api/runner")
        assert view.status_code == 200, view.text
        assert view.json()["pause"]["local"] is False, (runner.name, view.json()["pause"])


def _declining(hub: httpx.Client, runner: _Added) -> list[str]:
    """The levers the hub holds ``runner`` declining on, as its own read of itself serves them."""
    view = hub.get(f"/api/fleet/runners/{runner.runner_id}", headers={"Authorization": f"Bearer {runner.token}"})
    assert view.status_code == 200, view.text
    return [str(reason["lever"]) for reason in view.json()["declining"]]


def _stays_unleased(root: Path, runner: _Added, chunk_id: str, *, for_seconds: float) -> bool:
    """``runner`` never leases ``chunk_id`` in ``for_seconds`` — several of its ticks."""
    return not poll_until(lambda: _leased(root, runner.runner_id, chunk_id), timeout=for_seconds)


def test_a_usage_limit_holds_its_subscription_s_runners_until_a_sample_or_a_clear_opens_it(tmp_path: Path) -> None:
    bin_dir, workspace = _fixture(tmp_path)
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
        host_id, host_token = _add_host(hub)
        project_a, project_b, project_c = (_add_project(hub, slug) for slug in ("p-a", "p-b", "p-c"))
        runner_a = _add(hub, "runner-a", "p-a", host_id=host_id)
        runner_b = _add(hub, "runner-b", "p-b", host_id=host_id)
        runner_c = _add(hub, "runner-c", "p-c", host_id=host_id)
        everyone = (runner_a, runner_b, runner_c)
        spenders = ((runner_a, first), (runner_b, first), (runner_c, _SECOND))
        log = root / "daemon.log"
        with _two_plan_host(root, bin_dir, hub_port, workspace, host_token, spenders, plans) as local:
            # Before the limit: a sample placing its window, whose reset is the one the harness names.
            _sample(root, first, utilization_pct=90.0, resets_at=reset)
            _seed(hub, project_a, _limited_spec(f"{REPO}/issues/1", reset))

            assert poll_until(lambda: len(_marks(root, first)) == 1, timeout=90.0), read_daemon_log(log)
            [(limited_lease, held)] = _marks(root, first)
            assert held == _WINDOW, "the mark placed no held window"
            assert poll_until(lambda: _parked(root, limited_lease), timeout=30.0), read_daemon_log(log)

            # The limit holds both of its subscription's runners, and not the third.
            held_a, held_b = _seed(hub, project_a), _seed(hub, project_b)
            free_c = _seed(hub, project_c)
            assert poll_until(lambda: _leased(root, runner_c.runner_id, free_c), timeout=60.0), read_daemon_log(log)
            assert _stays_unleased(root, runner_b, held_b, for_seconds=5.0), read_daemon_log(log)
            assert not _leased(root, runner_a.runner_id, held_a)
            assert _parked(root, limited_lease)
            _assert_unbraked(local, everyone)
            # The hub shows the floor as the reason for the two it holds, and nothing for the third.
            assert poll_until(
                lambda: _declining(hub, runner_a) == ["floor"] and _declining(hub, runner_b) == ["floor"], timeout=30.0
            ), read_daemon_log(log)
            assert _declining(hub, runner_c) == []

            # The window turned over, under 100 percent: the floor opens before its reset, with nobody acting.
            _sample(root, first, utilization_pct=20.0, resets_at=next_reset)
            free_c = _seed(hub, project_c)
            assert poll_until(
                lambda: (
                    _woke(root, limited_lease)
                    and _leased(root, runner_a.runner_id, held_a)
                    and _leased(root, runner_b.runner_id, held_b)
                    and _leased(root, runner_c.runner_id, free_c)
                ),
                timeout=90.0,
            ), read_daemon_log(log)
            _assert_unbraked(local, everyone)
            assert poll_until(lambda: _declining(hub, runner_a) == [], timeout=30.0), read_daemon_log(log)

            # A second limit, on the other spender, in the new period.
            limited_again = _seed(hub, project_b, _limited_spec(f"{REPO}/issues/2", next_reset))
            assert poll_until(lambda: len(_marks(root, first)) == 2, timeout=90.0), read_daemon_log(log)
            relimited_lease, held = _marks(root, first)[1]
            assert held == _WINDOW
            assert _leased(root, runner_b.runner_id, limited_again)
            assert poll_until(lambda: _parked(root, relimited_lease), timeout=30.0), read_daemon_log(log)
            # A sample still showing the window shut in its current period.
            _sample(root, first, utilization_pct=100.0, resets_at=next_reset)
            held_a, held_b = _seed(hub, project_a), _seed(hub, project_b)
            free_c = _seed(hub, project_c)
            assert poll_until(lambda: _leased(root, runner_c.runner_id, free_c), timeout=60.0), read_daemon_log(log)
            assert _stays_unleased(root, runner_a, held_a, for_seconds=5.0), read_daemon_log(log)
            assert not _leased(root, runner_b.runner_id, held_b)
            _assert_unbraked(local, everyone)
            assert poll_until(lambda: _declining(hub, runner_a) == ["floor"], timeout=30.0), read_daemon_log(log)

            cleared = subprocess.run(
                [str(RUNNER_BIN), "clear-floor", "--subscription", first, "--dir", str(root)],
                capture_output=True,
                text=True,
                env=runner_spawn_env(f"http://127.0.0.1:{hub_port}"),
                timeout=60,
                check=False,
            )
            assert cleared.returncode == 0, cleared.stdout + cleared.stderr
            free_c = _seed(hub, project_c)
            assert poll_until(
                lambda: (
                    _woke(root, relimited_lease)
                    and _leased(root, runner_a.runner_id, held_a)
                    and _leased(root, runner_b.runner_id, held_b)
                    and _leased(root, runner_c.runner_id, free_c)
                ),
                timeout=90.0,
            ), read_daemon_log(log)
            assert _rows(root, "SELECT by FROM subscription_floor_clears WHERE slug = ?", first) == [("operator",)]
            _assert_unbraked(local, everyone)
            assert poll_until(lambda: _declining(hub, runner_a) == [], timeout=30.0), read_daemon_log(log)
