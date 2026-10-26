"""The forge pre-flight check against a real hub process: the startup log names a refusable record, and
``blizzard hub config check-forges`` names the same one while the hub serves its store (service tier). Run with
``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.config_inputs import init_hub
from tests.fleet_launchers import await_http, terminate
from tests.forge_check_support import seed_addresses
from tests.service.support import service_gate
from tests.support import daemon_log_sink, free_port, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

_HUB_BIN = Path(sys.executable).parent / "blizzard-hub"
_BLIZZARD_BIN = Path(sys.executable).parent / "blizzard"
_REFUSED_SLUG = "userful"


def test_the_startup_log_and_the_command_name_the_same_refusable_record(tmp_path: Path) -> None:
    import httpx2

    hub_dir = tmp_path / "hub"
    config = init_hub(hub_dir)
    seed_addresses(
        config.db_url,
        repository_rows=((_REFUSED_SLUG, "https://user@forge.example", "acme", "widget"),),
        sealed_secret=False,
    )
    port = free_port()
    log = hub_dir / "daemon.log"
    proc = subprocess.Popen(
        [str(_HUB_BIN), "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        await_http(proc, client, "/api/health", log=log)
        warned = [line for line in read_daemon_log(log).splitlines() if "forge migration would refuse" in line]
        assert any(_REFUSED_SLUG in line and "needs-cleaning" in line for line in warned), read_daemon_log(log)

        checked = subprocess.run(
            [str(_BLIZZARD_BIN), "hub", "config", "check-forges", "--dir", str(hub_dir)],
            capture_output=True,
            text=True,
        )
        assert checked.returncode == 1, checked.stdout + checked.stderr
        assert _REFUSED_SLUG in checked.stdout
    finally:
        client.close()
        terminate(proc)
