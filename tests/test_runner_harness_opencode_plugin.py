"""OpenCode plugin scaffold source and worker configuration checks.

Unit tests inspect generated source and config; a fake binary compares the adapter's
parsed output with and without a plugin reference. Neither executes the plugin hooks.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import Executor
from pathlib import Path

import pytest

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.internal.opencode_adapter import OpenCodeAdapter
from blizzard.runner.harness.internal.opencode_plugin import (
    LEASE_ENV_VARS,
    PLUGIN_DIRNAME,
    PLUGIN_FILENAME,
    SESSION_ENV_VAR,
    plugin_reference,
    render_plugin_source,
    write_plugin,
)
from blizzard.runner.harness.internal.opencode_shapes import parse_worker_config
from blizzard.runner.harness.internal.opencode_worker_config import render_worker_config, write_worker_config
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.worker_settings import HEARTBEAT_HOOK_COMMAND
from blizzard.runner.loop.process import LinuxProcessProbe
from tests.runner_fakes import make_envelope
from tests.support_opencode_binary import worker_binary

_BLIZZARD_ENV_NAME = re.compile(r"BLIZZARD_[A-Z_]+")


def _preamble(workdir: str, *, stdout_path: str) -> WorkerPreamble:
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=workdir)],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        stdout_path=stdout_path,
    )


# --------------------------------------------------------------------------- #
# Content shape (unit): the generated plugin text itself.


@pytest.mark.unit
def test_render_plugin_source_invokes_the_shared_heartbeat_command() -> None:
    source = render_plugin_source()
    assert HEARTBEAT_HOOK_COMMAND in source
    assert '"tool.execute.after"' in source
    assert '"shell.env"' in source


@pytest.mark.unit
def test_render_plugin_source_names_only_lease_identity_and_session_id_env_vars() -> None:
    """Only lease identity and session ID variable names appear in generated source."""
    source = render_plugin_source()
    names = set(_BLIZZARD_ENV_NAME.findall(source))
    assert names == set(LEASE_ENV_VARS) | {SESSION_ENV_VAR}


@pytest.mark.unit
def test_render_plugin_source_contains_two_try_and_catch_blocks() -> None:
    """The generated source contains two try blocks and two catch blocks."""
    source = render_plugin_source()
    assert source.count("try {") == 2
    assert source.count("} catch {") == 2


@pytest.mark.unit
def test_render_plugin_source_names_no_project_repository_path() -> None:
    source = render_plugin_source()
    assert "projects" not in source
    assert "blizzard-workspace" not in source
    assert "/home/" not in source


@pytest.mark.unit
def test_plugin_reference_uses_a_file_url_for_the_given_path(tmp_path: Path) -> None:
    path = tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME
    assert plugin_reference(path) == f"file://{path}"


@pytest.mark.unit
def test_write_plugin_persists_the_rendered_source(tmp_path: Path) -> None:
    path = tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME

    source = write_plugin(path)

    assert path.exists()
    assert path.read_text(encoding="utf-8") == source
    assert source == render_plugin_source()


@pytest.mark.unit
def test_scaffolded_worker_config_parses_the_plugin_reference(tmp_path: Path) -> None:
    plugin_path = tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME
    write_plugin(plugin_path)
    reference = plugin_reference(plugin_path)

    document = render_worker_config(plugins=(reference,))
    parsed = parse_worker_config(document)

    assert parsed.plugins == (reference,)


# --------------------------------------------------------------------------- #
# Fake-binary comparison (component): parsed output with and without a plugin reference.


@pytest.mark.component
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
            launcher=ProcessLauncher(probe, executor=spawn_executor),
            worker_config_path=worker_config_path,
        )
        pending = adapter.spawn(envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint")
        pending.confirm_durable()  # stands in for `Spawner.spawn`'s own call
        handle = pending.await_identity(5.0)
        os.waitpid(handle.pid, 0)
        output = stdout_path.read_text()
        return adapter.parse_verdict(output), adapter.parse_assessment(output), adapter.has_usable_output(output)

    without_plugin = _run(worker_config_path=None, label="bare")
    with_plugin = _run(worker_config_path=str(config_path), label="plugin")

    assert without_plugin == with_plugin
