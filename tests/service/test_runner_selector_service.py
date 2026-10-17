"""A local verb addresses the host's runner by ``--runner`` (service tier): a live ``blizzard-runner host`` of one
runner answers ``blizzard-runner status`` with ``--runner`` omitted, named, or given its id, and refuses an unmatched
``--runner`` naming the runners it holds. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.runner_join import RUNNER_BIN, runner_spawn_env, seed_pinned_runner
from tests.service.support import mock_hub, require_mock_fleet, service_gate
from tests.service.test_host_halted_runner_service import _CONFIGURED, _host
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]


def _status(root: Path, hub_url: str, *selector: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(RUNNER_BIN), "status", "--dir", str(root), *selector],
        capture_output=True,
        text=True,
        env=runner_spawn_env(hub_url),
        timeout=60,
        check=False,
    )


def test_status_addresses_a_host_of_ones_runner_unnamed_by_name_or_by_id_and_refuses_any_other(
    tmp_path: Path,
) -> None:
    bin_dir = require_mock_fleet()
    hub_port = free_port()
    hub_url = f"http://127.0.0.1:{hub_port}"
    root = tmp_path / "host"
    with mock_hub(bin_dir, hub_port) as hub:
        seed_pinned_runner(hub, _CONFIGURED)
        with _host(root, hub_url, _CONFIGURED.token):
            selected = [
                _status(root, hub_url, *selector)
                for selector in ([], ["--runner", _CONFIGURED.runner_name], ["--runner", _CONFIGURED.runner_id])
            ]
            unmatched = _status(root, hub_url, "--runner", "nope")

    for result in selected:
        assert result.returncode == 0, result.stdout + result.stderr
        assert _CONFIGURED.runner_name in result.stdout, result.stdout
    assert unmatched.returncode != 0
    said = unmatched.stdout + unmatched.stderr
    assert "holds no runner 'nope'" in said and f"{_CONFIGURED.runner_name} ({_CONFIGURED.runner_id}" in said, said
