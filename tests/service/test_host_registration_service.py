"""What a live host's runner registers, read back from the mock hub (service tier): its ``bindings`` and the
``subscription`` it names beside ``workspace_id``, ``env_capacity``, ``projects`` and its roster; and a runner naming
no subscription registers an empty roster, samples nothing, and its panel lists no subscription — nothing is
synthesized in its place. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import dataclasses
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import ProjectBinding, RunnerConfig
from blizzard.runner.hub.internal.token_file import HubTokenFile
from tests.fleet_launchers import await_http, terminate
from tests.host_files import init_runner_environment, sampler_without_credentials, write_host_file
from tests.runner_join import RUNNER_BIN, JoinedRunner, runner_spawn_env, seed_pinned_runner
from tests.service.support import mock_hub, poll_until, require_mock_fleet, service_gate
from tests.support import daemon_log_sink, free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_RUNNER = JoinedRunner(runner_id="rn_01JX0000000000000000000REGS", runner_name="runner-local", token="tok_registering")


@contextlib.contextmanager
def _host(root: Path, hub: httpx.Client, hub_url: str, *, subscribed: bool) -> Iterator[tuple[RunnerConfig, Any]]:
    """A live host whose one runner, with two agents, binds ``default`` and ``web`` in its workspace, naming the
    ``anthropic`` subscription when ``subscribed``; yields its config and a reader of the hub's own-runner view once
    the runner has registered."""
    config = init_runner_environment(root)
    port = free_port()
    configured = dataclasses.replace(
        config,
        host="127.0.0.1",
        port=port,
        hub_url=hub_url,
        runner_id=_RUNNER.runner_id,
        workspaces=tuple(
            dataclasses.replace(w, max_environments=4) if w.id == config.home_workspace.id else w
            for w in config.workspaces
        ),
        agents=2,
        bindings=(
            ProjectBinding("default", config.home_workspace.id),
            ProjectBinding("web", config.home_workspace.id),
        ),
        subscriptions=sampler_without_credentials(root) if subscribed else (),
    )
    write_host_file(configured)
    HubTokenFile.of(root, configured.token_env).write(_RUNNER.token)
    log = root / "daemon.log"
    proc = subprocess.Popen(
        [str(RUNNER_BIN), "host", "--dir", str(root)],
        env=runner_spawn_env(hub_url, {"BZ_RUNNER_TICK_SECONDS": "0.5"}),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    local = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)

    def view() -> httpx.Response:
        return hub.get(f"/api/fleet/runners/{_RUNNER.runner_id}", headers=_RUNNER.bearer)

    try:
        await_http(proc, local, "/api/health", log=log)
        assert poll_until(lambda: view().status_code == 200, timeout=30.0), read_daemon_log(log)
        yield configured, (view, local)
    finally:
        local.close()
        terminate(proc)


def _sampled_slugs(config: RunnerConfig) -> list[str]:
    """The slug of every sampling attempt the host's store records, a miss included."""
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.connect() as conn:
            return [slug for (slug,) in conn.execute(sa.text("SELECT slug FROM external_usage_samples"))]
    finally:
        engine.dispose()


def _seed_world(hub: httpx.Client) -> None:
    assert hub.post("/_seed/projects", json={"slug": "web", "name": "Web"}).status_code == 201
    seed_pinned_runner(hub, _RUNNER)


def test_the_hub_reads_back_a_runners_bindings_and_subscription_beside_its_registration(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        _seed_world(hub)
        with _host(tmp_path / "host", hub, f"http://127.0.0.1:{hub_port}", subscribed=True) as (config, (view, _)):
            runner = view().json()
            # The named subscription is sampled: its credential-less attempt records a miss.
            assert poll_until(lambda: "anthropic" in _sampled_slugs(config), timeout=30.0)

    assert runner["workspace_id"] == config.home_workspace.id
    assert runner["env_capacity"] == 4
    assert [project["entry"] for project in runner["projects"]] == ["default", "web"]
    assert [(b["project"], b["workspace_id"], b["agents"]) for b in runner["bindings"]] == [
        ("default", config.home_workspace.id, 2),
        ("web", config.home_workspace.id, 2),
    ]
    assert runner["agents"] == 2
    assert (runner["subscription"], runner["subscription_unrostered"]) == ("anthropic", False)
    assert [subscription["slug"] for subscription in runner["subscriptions"]] == ["anthropic"], runner


def test_a_runner_naming_no_subscription_registers_an_empty_roster_and_samples_nothing(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        _seed_world(hub)
        with _host(tmp_path / "host", hub, f"http://127.0.0.1:{hub_port}", subscribed=False) as (
            config,
            (view, local),
        ):
            # Long enough for several ticks, each a chance to sample.
            time.sleep(3.0)
            runner = view().json()
            panel = local.get("/api/host/subscriptions")
            assert panel.status_code == 200, panel.text
            listed = panel.json()

    assert runner["subscription"] is None
    assert runner["subscriptions"] == []
    assert listed["items"] == [], listed
    assert _sampled_slugs(config) == []
