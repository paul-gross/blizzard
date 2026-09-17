"""The runner-owned OpenCode plugin scaffold (execution spec, "Runner-owned plugin", D7 phase
4) — content shape (unit) and its degrade-only effect on the adapter's own parsed turn outcome
(component). The plugin's JS/TS *behavior* (that a raising callback never escapes its own
``try``/``catch``) is proven structurally here rather than by running a JS engine: this repo's
test toolchain (``mise.toml``) pins no node/bun, so the generated source's shape — exactly one
``try``/``catch`` per hook body — is the checkable surface, mirroring how ``config.py``'s
``blizzard-runner.toml`` scaffold is tested as generated text, not executed."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerPreamble
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
def test_render_plugin_source_forwards_exactly_the_lease_identity_and_session_id() -> None:
    """The acceptance bar, verbatim: the environment values reaching a tool subprocess are
    exactly the lease identity plus the authoritative session id — nothing broader."""
    source = render_plugin_source()
    names = set(_BLIZZARD_ENV_NAME.findall(source))
    assert names == set(LEASE_ENV_VARS) | {SESSION_ENV_VAR}


@pytest.mark.unit
def test_render_plugin_source_wraps_every_hook_body_in_try_catch() -> None:
    """Both jobs are degrade-only by construction: a thrown error inside either hook body is
    caught before it can ever reach OpenCode's own dispatcher — the channel this pinned
    version cannot prove live (``contracts/opencode/1.18.25/live_diagnostic.json``'s
    ``root_hook: absent``) is never allowed to matter."""
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
def test_scaffolded_worker_config_carries_the_plugin_reference_and_nothing_else(tmp_path: Path) -> None:
    plugin_path = tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME
    write_plugin(plugin_path)
    reference = plugin_reference(plugin_path)

    document = render_worker_config(plugins=(reference,))
    parsed = parse_worker_config(document)

    assert parsed.plugins == (reference,)


# --------------------------------------------------------------------------- #
# Degrade-only (component): a plugin-bearing worker config changes nothing the adapter parses.


@pytest.mark.component
def test_a_plugin_bearing_worker_config_does_not_change_the_adapters_parsed_turn(tmp_path: Path) -> None:
    """Nothing in the adapter's own code ever reads a hook's outcome (unit-test's own
    invariant, above): a worker config that names the scaffolded plugin — even one this fake
    binary never actually loads or executes as JS — must leave the parsed verdict,
    assessment, and usability identical to a run with no worker config at all."""
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
        adapter = OpenCodeAdapter(binary=binary, process=LinuxProcessProbe(), worker_config_path=worker_config_path)
        handle = adapter.spawn(
            envelope, _preamble(str(workdir), stdout_path=str(stdout_path)), session_hint="hint"
        ).await_identity(5.0)
        os.waitpid(handle.pid, 0)
        output = stdout_path.read_text()
        return adapter.parse_verdict(output), adapter.parse_assessment(output), adapter.has_usable_output(output)

    without_plugin = _run(worker_config_path=None, label="bare")
    with_plugin = _run(worker_config_path=str(config_path), label="plugin")

    assert without_plugin == with_plugin
