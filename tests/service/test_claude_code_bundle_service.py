"""Claude Code bundle delivery, service tier: a runner with an operator bundle drives the mock
Claude Code worker, which executes the composed ``--settings`` hooks.

The mock worker runs whatever hook commands the settings it is handed declare, so the operator's
marker hook firing proves the composed file was delivered and the stub ``blizzard`` on the worker's
PATH recording a heartbeat proves composition kept the runner's own wiring beside it."""

from __future__ import annotations

import dataclasses
import json
import os
import stat
import time
from pathlib import Path

import pytest

from blizzard.runner.harness.bundle_layouts import publish_harness_bundle
from blizzard.runner.loop.build import LoopWiring
from tests.e2e.test_acceptance_loop import _free_port, _runner_config
from tests.service.support import (
    mint_fixture,
    mock_hub,
    mock_hub_chunk_spec,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)
from tests.service.test_runner_service import _WORK_REF_URL, _status, _tick_env

pytestmark = [pytest.mark.service, service_gate]


def test_a_bundled_runners_mock_worker_runs_the_operators_and_the_runners_hooks(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    marker = tmp_path / "operator-hook.log"
    beats = tmp_path / "heartbeats.log"
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    stub = stubs / "blizzard"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {beats}\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    bundle = tmp_path / "bundle"
    (bundle / "claude-code").mkdir(parents=True)
    operator_hook = {"hooks": [{"type": "command", "command": f"sh -c 'echo fired >> {marker}'"}]}
    (bundle / "claude-code" / "settings.json").write_text(json.dumps({"hooks": {"PostToolUse": [operator_hook]}}))
    (bundle / "claude-code" / "mcp.json").write_text("{}")

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        resp = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_WORK_REF_URL))
        assert resp.status_code == 201, resp.text
        chunk_id = resp.json()["chunk_id"]
        config = dataclasses.replace(
            _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port), worker_path_prepend=(str(stubs),)
        )
        # What a bundled `tick` builds: the published snapshot reaches the adapter through the wiring.
        wiring = LoopWiring.of(config, bundle=publish_harness_bundle(bundle, config.root))

        landed = poll_until(lambda: _tick(wiring, fenced) or _status(hub, chunk_id) == "done", timeout=90.0)
        assert landed, f"chunk did not land (status {_status(hub, chunk_id)!r})"

    assert marker.exists(), "the operator's hook never fired: the composed settings did not reach the worker"
    assert "runner heartbeat" in beats.read_text(), "the runner's heartbeat hook was lost in composition"


def _tick(wiring: LoopWiring, fenced: dict[str, str]) -> None:
    """One reconciliation pass with the harness fence set, then a short wait for the worker."""
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        wiring.tick_once()
        time.sleep(0.3)
    finally:
        os.environ.clear()
        os.environ.update(prior)
