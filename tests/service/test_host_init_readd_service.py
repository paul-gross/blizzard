"""``blizzard-runner init --allow-readd`` over what a pre-host runner left behind (service tier): a directory holding
a pre-host ``data/runner.db`` and its token under the unsuffixed variable, with no config beside them. Without the
flag ``init`` refuses, names graduation, and changes nothing; with it the store is set aside, a runner is added at the
mock hub, its id lands in the host file, its token under ``BZ_HUB_TOKEN_<NAME>`` and its identity row in the host's
store, and ``host --dir`` then starts a host. Over the host ``init`` registers, ``runner add`` — holding no hub
session — adds a runner the mock holds under the host and the host runs after a restart, and ``runner retire`` halts it
on the running host within a tick and removes its entry, after which ``init`` runs again and a restarted host serves the
runner that remains. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.host_config import HOST_TOKEN_VARIABLE, HostConfig
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.runtime import init_environment, migration_runner
from tests.fleet_launchers import await_http, terminate
from tests.host_files import sampler_without_credentials, write_host_file
from tests.runner_fakes import make_store
from tests.runner_join import RUNNER_BIN, run_runner_init, runner_spawn_env, token_identity
from tests.runner_scope_seeding import reflect
from tests.service.support import mock_hub, poll_until, require_mock_fleet, service_gate
from tests.support import daemon_log_sink, free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_PARENT = "20261011_1000_runner_scope"
_PRE_HOST_RUNNER_ID = "rn_01JX000000000000000000LEFT"
_PRE_HOST_TOKEN = "tok_pre_host"


def _left_behind(root: Path) -> None:
    """A pre-host runner's store at ``data/runner.db``, its identity row naming the pre-host runner, and its token
    under the unsuffixed variable — the config that named them both cleared away."""
    config = init_environment(root)
    migration_runner(config).downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.insert(reflect(engine).tables["runner_identity"]).values(
                    runner_id=_PRE_HOST_RUNNER_ID, runner_name="runner-local", registered_at=datetime.now(UTC)
                )
            )
    finally:
        engine.dispose()
    layout = HostLayout(root)
    layout.store_path.rename(layout.legacy_store_path)
    layout.config_path.unlink()
    (root / ".env").write_text(f"BZ_HUB_TOKEN={_PRE_HOST_TOKEN}\n")


def _tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_init_with_allow_readd_sets_a_pre_host_store_aside_adds_a_runner_and_the_host_starts(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "runner"
    _left_behind(root)
    layout = HostLayout(root)
    before = _tree(root)

    with mock_hub(bin_dir, hub_port) as hub:
        refused = run_runner_init(root, hub_url)
        said = refused.stdout + refused.stderr
        assert refused.returncode != 0, said
        assert "graduation procedure in docs/upgrade.md" in said and "--allow-readd" in said, said
        assert _tree(root) == before

        readded = run_runner_init(root, hub_url, allow_readd=True)
        assert readded.returncode == 0, readded.stdout + readded.stderr

        assert not layout.legacy_store_path.exists()
        assert [path.name for path in layout.data_dir.glob("runner.db.*")] != []
        config = HostConfig.load(root).runner_config()
        assert config.runner_id is not None and config.runner_id != _PRE_HOST_RUNNER_ID
        assert config.token_env == hub_token_variable(config.name)
        token = HubTokenFile.of(root, config.token_env).held()
        identity = token_identity(hub_url, token)
        assert identity.status_code == 200, identity.text
        assert identity.json()["runner_id"] == config.runner_id
        row = make_store(config.db_url, runner_id=config.runner_id).runner_identity()
        assert row is not None and (row.runner_id, row.runner_name) == (config.runner_id, config.name)

        port = free_port()
        write_host_file(dataclasses.replace(config, host="127.0.0.1", port=port, subscriptions=()))
        log = root / "daemon.log"
        proc = subprocess.Popen(
            [str(RUNNER_BIN), "host", "--dir", str(root)],
            env=runner_spawn_env(hub_url, {"BZ_RUNNER_TICK_SECONDS": "0.5"}),
            stdout=daemon_log_sink(log),
            stderr=subprocess.STDOUT,
            text=True,
        )
        local = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
        try:
            await_http(proc, local, "/api/health", log=log)
            own_read = f"/api/fleet/runners/{config.runner_id}"
            bearer = {"Authorization": f"Bearer {token}"}
            assert poll_until(lambda: hub.get(own_read, headers=bearer).status_code == 200, timeout=30.0), (
                read_daemon_log(log)
            )
            assert local.get(f"/runners/{config.runner_id}/api/runner").json()["runner_id"] == config.runner_id
        finally:
            local.close()
            terminate(proc)


def _runner_verb(root: Path, hub_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """``blizzard runner <args> --dir <root>`` holding no hub session: its config directory is a fresh one."""
    env = runner_spawn_env(hub_url, {"XDG_CONFIG_HOME": str(root.parent / "no-session")})
    return subprocess.run(
        [str(RUNNER_BIN), *args, "--dir", str(root)], capture_output=True, text=True, env=env, check=False, timeout=60
    )


@contextlib.contextmanager
def _running_host(root: Path, hub_url: str, port: int, log: Path) -> Iterator[httpx.Client]:
    proc = subprocess.Popen(
        [str(RUNNER_BIN), "host", "--dir", str(root)],
        env=runner_spawn_env(hub_url, {"BZ_RUNNER_TICK_SECONDS": "0.5"}),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    local = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
    try:
        await_http(proc, local, "/api/health", log=log)
        yield local
    finally:
        local.close()
        terminate(proc)


def _identity_state(local: httpx.Client, runner_id: str) -> str | None:
    view = local.get(f"/runners/{runner_id}/api/runner")
    return view.json()["identity"]["state"] if view.status_code == 200 else None


def test_runner_add_with_no_session_runs_after_a_restart_and_retire_halts_it_within_a_tick(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    hub_port = free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "host"
    port = free_port()
    log = root / "daemon.log"

    with mock_hub(bin_dir, hub_port) as hub:
        joined = run_runner_init(root, hub_url)
        assert joined.returncode == 0, joined.stdout + joined.stderr
        host_token = HubTokenFile.of(root, HOST_TOKEN_VARIABLE).held()
        assert host_token
        bearer = {"Authorization": f"Bearer {host_token}"}
        config = HostConfig.load(root)
        HostLayout(root).config_path.write_text(
            dataclasses.replace(
                config, host="127.0.0.1", port=port, subscriptions=sampler_without_credentials(root)
            ).to_toml()
        )

        with _running_host(root, hub_url, port, log):
            added = _runner_verb(root, hub_url, "add", "r2", "--project", "default")
            assert added.returncode == 0, added.stdout + added.stderr
            assert "it starts runner r2 at its next start" in added.stdout, added.stdout
        r2 = HostConfig.load(root).sole_runner("r2").id
        assert r2 is not None
        record = hub.get("/api/fleet/host", headers=bearer)
        assert record.status_code == 200, record.text
        assert {r["runner_id"] for r in record.json()["runners"]} == {config.runners[0].id, r2}, record.json()
        # init run again writes r2's identity row, so the retire below must leave a store its host still starts.
        rejoined = run_runner_init(root, hub_url)
        assert rejoined.returncode == 0, rejoined.stdout + rejoined.stderr

        with _running_host(root, hub_url, port, log) as local:
            assert poll_until(lambda: _identity_state(local, r2) == "matched", timeout=30.0), (
                _identity_state(local, r2),
                read_daemon_log(log),
            )

            retired = _runner_verb(root, hub_url, "retire", "--runner", "r2")
            assert retired.returncode == 0, retired.stdout + retired.stderr
            assert "it halts runner r2 at its next tick" in retired.stdout, retired.stdout
            assert [entry.name for entry in HostConfig.load(root).runners] == [config.runners[0].name]

            def halted() -> bool:
                listed = local.get("/api/host/runners").json()["items"]
                return any(item["runner_id"] == r2 and item["state"] == "halted" for item in listed)

            assert poll_until(halted, timeout=10.0), (local.get("/api/host/runners").json(), read_daemon_log(log))
            assert _identity_state(local, config.runners[0].id or "") == "matched"

        again = run_runner_init(root, hub_url)
        assert again.returncode == 0, again.stdout + again.stderr
        with _running_host(root, hub_url, port, log) as local:
            remaining = config.runners[0].id or ""
            assert poll_until(lambda: _identity_state(local, remaining) == "matched", timeout=30.0), (
                local.get("/api/host/runners").json(),
                read_daemon_log(log),
            )
            listed = local.get("/api/host/runners").json()["items"]
            assert r2 not in {item["runner_id"] for item in listed}, listed
