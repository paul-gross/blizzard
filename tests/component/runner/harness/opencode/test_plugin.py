"""OpenCode plugin scaffold source and worker configuration checks.

Unit tests inspect generated source and config; a fake binary compares the adapter's
parsed output with and without a plugin reference. Neither executes the plugin hooks.
"""

from __future__ import annotations

from concurrent.futures import Executor
from pathlib import Path

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.opencode.adapter import OpenCodeAdapter
from blizzard.runner.harness.opencode.plugin import (
    PLUGIN_DIRNAME,
    PLUGIN_FILENAME,
    plugin_reference,
    write_plugin,
)
from blizzard.runner.harness.opencode.worker_config import write_worker_config
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from tests.runner_fakes import make_envelope
from tests.support_child_launch import CHILDREN, version_probe, wait_for_exit
from tests.support_opencode_binary import worker_binary


def _preamble(workdir: str, *, stdout_path: str) -> WorkerPreamble:
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=workdir)],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        stdout_path=stdout_path,
    )


# --------------------------------------------------------------------------- #
# Content shape (unit): the generated plugin text itself.


def test_a_plugin_reference_does_not_change_fake_binary_output_parsing(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    """With a fake binary that does not load plugins, compare parsed output for both configs."""
    binary = worker_binary(tmp_path, minted_session_id="ses_plugin_proof")
    workdir = tmp_path / "e1"
    workdir.mkdir()

    plugin_path = tmp_path / "runtime" / PLUGIN_DIRNAME / PLUGIN_FILENAME
    write_plugin(plugin_path)
    config_path = tmp_path / "runtime" / "opencode-worker-config.json"
    write_worker_config(config_path, plugins=(plugin_reference(plugin_path),))

    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    def _run(*, worker_config_path: str | None, label: str) -> tuple[str | None, str, bool]:
        stdout_path = tmp_path / f"lease-{label}.stdout"
        probe = LinuxProcessProbe()
        adapter = OpenCodeAdapter(
            worker_env=AllowlistedEnv.of(()),
            binary=binary,
            process=probe,
            launcher=ProcessLauncher(probe, executor=spawn_executor, children=CHILDREN),
            worker_config_path=worker_config_path,
            versions=version_probe(),
        )
        pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")
        pending.confirm_durable()  # stands in for `Spawner.spawn`'s own call
        handle = pending.await_identity(5.0)
        wait_for_exit(handle.pid)
        output = stdout_path.read_text()
        return adapter.parse_verdict(output), adapter.parse_assessment(output), adapter.has_usable_output(output)

    without_plugin = _run(worker_config_path=None, label="bare")
    with_plugin = _run(worker_config_path=str(config_path), label="plugin")

    assert without_plugin == with_plugin
