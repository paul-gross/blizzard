"""A runner the host cannot run is halted, and the host serves on (service tier): a live ``blizzard-runner host``
whose one runner holds a token the mock hub resolves to another runner, or whose entry carries a runner-only config
error, keeps answering ``/api/health`` and names the halt in ``blizzard-runner status``. The mismatched runner is
built and its tick gated, so ``/api/runner``'s identity verdict carries its halt; the misconfigured one is left
unbuilt, so ``/api/ready``'s detail names it. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import dataclasses
import sqlite3
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.runtime import sqlite_store_file
from tests.e2e.test_acceptance_loop import _await_http, _free_port, _terminate
from tests.host_files import init_runner_environment, write_host_file
from tests.runner_join import RUNNER_BIN, JoinedRunner, runner_spawn_env, seed_pinned_runner
from tests.service.support import mock_hub, poll_until, require_mock_fleet, service_gate
from tests.support import daemon_log_sink, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_CONFIGURED = JoinedRunner(runner_id="rn_01JX00000000000000000HALTA", runner_name="runner-local", token="tok_halt_a")
_OTHER = JoinedRunner(runner_id="rn_01JX00000000000000000HALTB", runner_name="runner-other", token="tok_halt_b")


@contextlib.contextmanager
def _host(
    root: Path, hub_url: str, token: str, *, subscription: str | None = None, store_names: str | None = None
) -> Iterator[httpx.Client]:
    """``blizzard-runner host`` over ``root``, its one runner configured as ``_CONFIGURED`` and holding ``token``,
    naming ``subscription`` when given and over a store whose identity row names ``store_names`` when given; yields a client to its port once it answers ``/api/health``."""
    port = _free_port()
    config = init_runner_environment(root)
    write_host_file(
        dataclasses.replace(
            config, host="127.0.0.1", port=port, hub_url=hub_url, runner_id=_CONFIGURED.runner_id, subscriptions=()
        )
    )
    if subscription is not None:
        path = HostLayout(root).config_path
        named = f'name = "{config.name}"\n'
        path.write_text(path.read_text().replace(named, f'{named}subscription = "{subscription}"\n', 1))
    HubTokenFile.of(root, config.token_env).write(token)
    if store_names is not None:
        with contextlib.closing(sqlite3.connect(sqlite_store_file(config.db_url))) as conn, conn:
            conn.execute(
                "INSERT INTO runner_identity (runner_id, runner_name, registered_at) "
                "VALUES (?, 'runner-local', '2026-10-13T00:00:00+00:00')",
                (store_names,),
            )
    log = root / "daemon.log"
    proc = subprocess.Popen(
        [str(RUNNER_BIN), "host", "--dir", str(root)],
        env=runner_spawn_env(hub_url, {"BZ_RUNNER_TICK_SECONDS": "0.5"}),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
    try:
        _await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        _terminate(proc)


def _identity_state(local: httpx.Client) -> str | None:
    view = local.get("/api/runner")
    return view.json()["identity"]["state"] if view.status_code == 200 else None


def _status(root: Path, hub_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUNNER_BIN), "status", "--dir", str(root)],
        capture_output=True,
        text=True,
        env=runner_spawn_env(hub_url),
        timeout=60,
        check=False,
    )


def test_a_token_that_resolves_to_another_runner_halts_that_runner_while_the_host_serves(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = _free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        seed_pinned_runner(hub, _CONFIGURED)
        seed_pinned_runner(hub, _OTHER)
        with _host(root, hub_url, _OTHER.token) as local:
            assert poll_until(lambda: _identity_state(local) == "mismatched", timeout=30.0), read_daemon_log(
                root / "daemon.log"
            )
            assert local.get("/api/health").status_code == 200
            status = _status(root, hub_url)
            said = status.stdout + status.stderr
            assert f"REFUSING: token belongs to runner {_OTHER.runner_id}" in said, said
            assert "BZ_HUB_TOKEN_RUNNER_LOCAL" in said and "[[runner]] id" in said, said
            # The halted runner does no work under the token it holds: it never registers as the runner it names.
            unregistered = hub.get(f"/api/fleet/runners/{_OTHER.runner_id}", headers=_OTHER.bearer)
            assert unregistered.status_code == 409, unregistered.text


def test_a_runner_only_config_error_halts_that_runner_while_the_host_serves(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = _free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        seed_pinned_runner(hub, _CONFIGURED)
        with _host(root, hub_url, _CONFIGURED.token, subscription="missing") as local:
            assert local.get("/api/health").status_code == 200
            detail = local.get("/api/ready").json()["detail"]
            assert "runner-local" in detail and "names subscription 'missing'" in detail, detail
            assert local.get("/api/runner").status_code != 200
            status = _status(root, hub_url)
            said = status.stdout + status.stderr
            assert status.returncode != 0
            assert "serves no runner" in said and "names subscription 'missing'" in said, said


def test_a_store_that_names_another_runner_halts_that_runner_while_the_host_serves(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = _free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        seed_pinned_runner(hub, _CONFIGURED)
        with _host(root, hub_url, _CONFIGURED.token, store_names=_OTHER.runner_id) as local:
            assert local.get("/api/health").status_code == 200
            detail = local.get("/api/ready").json()["detail"]
            assert _OTHER.runner_id in detail and _CONFIGURED.runner_id in detail, detail
            assert local.get("/api/runner").status_code != 200
            # Nothing registered under the configured id: the store still names only the one runner.
            unregistered = hub.get(f"/api/fleet/runners/{_CONFIGURED.runner_id}", headers=_CONFIGURED.bearer)
            assert unregistered.status_code == 409, unregistered.text
