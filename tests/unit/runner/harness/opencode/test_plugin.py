"""OpenCode plugin scaffold source and worker configuration checks.

Unit tests inspect generated source and config; a fake binary compares the adapter's
parsed output with and without a plugin reference. Neither executes the plugin hooks.
"""

from __future__ import annotations

import re
from pathlib import Path

from blizzard.runner.harness.opencode.plugin import (
    LEASE_ENV_VARS,
    PLUGIN_DIRNAME,
    PLUGIN_FILENAME,
    SESSION_ENV_VAR,
    plugin_reference,
    render_plugin_source,
    write_plugin,
)
from blizzard.runner.harness.opencode.shapes import parse_worker_config
from blizzard.runner.harness.opencode.worker_config import render_worker_config
from blizzard.runner.harness.worker_hooks import HEARTBEAT_HOOK_COMMAND

_BLIZZARD_ENV_NAME = re.compile(r"BLIZZARD_[A-Z_]+")


def test_render_plugin_source_invokes_the_shared_heartbeat_command() -> None:
    source = render_plugin_source()
    assert HEARTBEAT_HOOK_COMMAND in source
    assert '"tool.execute.after"' in source
    assert '"shell.env"' in source


def test_render_plugin_source_names_only_lease_identity_and_session_id_env_vars() -> None:
    """Only lease identity and session ID variable names appear in generated source."""
    source = render_plugin_source()
    names = set(_BLIZZARD_ENV_NAME.findall(source))
    assert names == set(LEASE_ENV_VARS) | {SESSION_ENV_VAR}


def test_render_plugin_source_contains_two_try_and_catch_blocks() -> None:
    """The generated source contains two try blocks and two catch blocks."""
    source = render_plugin_source()
    assert source.count("try {") == 2
    assert source.count("} catch {") == 2


def test_render_plugin_source_names_no_project_repository_path() -> None:
    source = render_plugin_source()
    assert "projects" not in source
    assert "blizzard-workspace" not in source
    assert "/home/" not in source


def test_plugin_reference_uses_a_file_url_for_the_given_path(tmp_path: Path) -> None:
    path = tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME
    assert plugin_reference(path) == f"file://{path}"


def test_write_plugin_persists_the_rendered_source(tmp_path: Path) -> None:
    path = tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME

    source = write_plugin(path)

    assert path.exists()
    assert path.read_text(encoding="utf-8") == source
    assert source == render_plugin_source()


def test_scaffolded_worker_config_parses_the_plugin_reference(tmp_path: Path) -> None:
    plugin_path = tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME
    write_plugin(plugin_path)
    reference = plugin_reference(plugin_path)

    document = render_worker_config(plugins=(reference,))
    parsed = parse_worker_config(document)

    assert parsed.plugins == (reference,)


# --------------------------------------------------------------------------- #
# Fake-binary comparison (component): parsed output with and without a plugin reference.
