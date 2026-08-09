"""Composition-root threading (``bzh:dependency-injection``) — issue #88.

Each case pins one ``RunnerConfig`` key reaching the collaborator built from it: an
unthreaded key is read from the operator's toml and dropped, which no other tier sees.
Both roots that build a ``ClaudeCodeAdapter`` are covered (issue #276).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.app import build_hosted_app
from blizzard.runner.config import CONFIG_FILENAME, ConfigError, RunnerConfig
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.loop.build import LoopWiring, PeriodicDriver
from tests.runner_fakes import FakeHub


@pytest.mark.unit
def test_loop_wiring_threads_worker_env_passthrough_into_the_adapter(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        worker_env_passthrough=("MY_HARNESS_QUIRK", "ANOTHER_VAR"),
    )

    ctx = LoopWiring(config, "", "").context(FakeHub())

    assert isinstance(ctx.harness, ClaudeCodeAdapter)
    assert ctx.harness._env_passthrough == ("MY_HARNESS_QUIRK", "ANOTHER_VAR")


@pytest.mark.unit
def test_loop_wiring_threads_external_usage_credentials_path_into_the_adapter(tmp_path: Path) -> None:
    """An unthreaded override leaves every daemon this root builds reading the
    adapter's own default credentials path and reaching the real Anthropic endpoint
    (issue #218)."""
    scratch = str(tmp_path / "scratch-credentials.json")
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        external_usage_credentials_path=scratch,
    )

    ctx = LoopWiring(config, "", "").context(FakeHub())

    assert isinstance(ctx.harness, ClaudeCodeAdapter)
    assert ctx.harness._credentials_path == scratch


@pytest.mark.unit
def test_loop_wiring_threads_the_worker_settings_path_and_permission_mode(tmp_path: Path) -> None:
    """Both reach the spawned worker only as adapter argv flags (``--settings``,
    ``--permission-mode``), so dropping either threading leaves every worker running
    without the runner-owned hook file, or at the interactive permission default."""
    settings = str(tmp_path / "worker-settings.json")
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        worker_settings_path=settings,
        harness_permission_mode="acceptEdits",
    )

    ctx = LoopWiring(config, "", "").context(FakeHub())

    assert isinstance(ctx.harness, ClaudeCodeAdapter)
    assert ctx.harness._settings_path == settings
    assert ctx.harness._permission_mode == "acceptEdits"


@pytest.mark.unit
def test_hosted_app_threads_the_worker_settings_path_and_permission_mode(tmp_path: Path) -> None:
    """The hosted app builds its own adapter, and the takeover command it composes
    asserts the permission mode (issue #258) — a second threading of the same two keys,
    which the loop's own root does not cover."""
    settings = str(tmp_path / "worker-settings.json")
    (tmp_path / CONFIG_FILENAME).write_text(
        f'db_url = "{RunnerConfig.default_db_url(tmp_path)}"\n'
        f'worker_settings_path = "{settings}"\n'
        'harness_permission_mode = "acceptEdits"\n'
    )

    app = build_hosted_app(RunnerConfig.load(tmp_path))

    harness = app.state.harness
    assert isinstance(harness, ClaudeCodeAdapter)
    assert harness._settings_path == settings
    assert " --permission-mode acceptEdits" in harness.resume_command("/w", "s-1", attended=True)


@pytest.mark.unit
def test_loop_wiring_threads_runner_dir_from_the_resolved_root(tmp_path: Path) -> None:
    """The wrapped takeover command (issue #251) needs ``LoopConfig.runner_dir`` to
    mirror ``RunnerConfig``'s resolved ``root``. Routed through ``RunnerConfig.load()``
    with an un-resolved ``..``-bearing path, since a bare ``tmp_path`` already resolves."""
    real_root = tmp_path / "runner"
    real_root.mkdir()
    (real_root / CONFIG_FILENAME).write_text(f'db_url = "{RunnerConfig.default_db_url(real_root)}"\n')
    unresolved_root = tmp_path / "nested" / ".." / "runner"

    config = RunnerConfig.load(unresolved_root)
    ctx = LoopWiring(config, "", "").context(FakeHub())

    assert ".." not in ctx.config.runner_dir
    assert ctx.config.runner_dir == str(real_root.resolve())


@pytest.mark.unit
def test_periodic_driver_resolves_prompts_eagerly_at_construction(tmp_path: Path) -> None:
    """A configured-but-missing ``runner_prompt_file`` must raise ``ConfigError`` from
    the constructor, on the caller's own thread — not from inside the background loop
    thread, where it would silently kill the loop while uvicorn keeps serving."""
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        runner_prompt_file="does-not-exist.md",
    )

    with pytest.raises(ConfigError):
        PeriodicDriver(config, interval_seconds=30.0)
