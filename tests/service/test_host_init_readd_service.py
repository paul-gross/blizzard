"""``blizzard-runner init --allow-readd`` over what a pre-host runner left behind (service tier): a directory holding
a pre-host ``data/runner.db`` and its token under the unsuffixed variable, with no config beside them. Without the
flag ``init`` refuses, names graduation, and changes nothing; with it the store is set aside, a runner is added at the
mock hub, its id lands in the host file, its token under ``BZ_HUB_TOKEN_<NAME>`` and its identity row in the host's
store, and ``host --dir`` then starts a host. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import dataclasses
import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.runtime import init_environment, migration_runner
from tests.e2e.test_acceptance_loop import _await_http, _free_port, _terminate
from tests.host_files import write_host_file
from tests.runner_fakes import make_store
from tests.runner_join import RUNNER_BIN, run_runner_init, runner_spawn_env, token_identity
from tests.runner_scope_seeding import reflect
from tests.service.support import mock_hub, poll_until, require_mock_fleet, service_gate
from tests.support import daemon_log_sink, read_daemon_log

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
    hub_port = _free_port()
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

        port = _free_port()
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
            _await_http(proc, local, "/api/health", log=log)
            own_read = f"/api/fleet/runners/{config.runner_id}"
            bearer = {"Authorization": f"Bearer {token}"}
            assert poll_until(lambda: hub.get(own_read, headers=bearer).status_code == 200, timeout=30.0), (
                read_daemon_log(log)
            )
            assert local.get(f"/runners/{config.runner_id}/api/runner").json()["runner_id"] == config.runner_id
        finally:
            local.close()
            _terminate(proc)
