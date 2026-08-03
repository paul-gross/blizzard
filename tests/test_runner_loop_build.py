"""``build_loop_context`` wiring (``bzh:dependency-injection``) — issue #88.

The composition root threads ``RunnerConfig.worker_env_passthrough`` into the
``ClaudeCodeAdapter`` it constructs, so the operator's ``[worker] env_passthrough``
toml key actually reaches the spawn-environment allowlist rather than being read and
dropped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.config import ConfigError, RunnerConfig
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.loop.build import PeriodicDriver, build_loop_context
from tests.runner_fakes import FakeHub


@pytest.mark.unit
def test_build_loop_context_threads_worker_env_passthrough_into_the_adapter(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        worker_env_passthrough=("MY_HARNESS_QUIRK", "ANOTHER_VAR"),
    )

    ctx = build_loop_context(config, FakeHub(), workspace_prompt="", runner_prompt="")

    assert isinstance(ctx.harness, ClaudeCodeAdapter)
    assert ctx.harness._env_passthrough == ("MY_HARNESS_QUIRK", "ANOTHER_VAR")


@pytest.mark.unit
def test_build_loop_context_threads_external_usage_credentials_path_into_the_adapter(tmp_path: Path) -> None:
    """A composition-root gap here is a network-isolation gap, not just a wiring miss:
    an unthreaded override leaves every daemon this root builds — including the real
    subprocess service/e2e/journey/crash-sweep tiers spawn — reading the adapter's own
    default (``~/.claude/.credentials.json``) and reaching the real Anthropic endpoint
    on its sampling cadence, silently breaking those tiers' no-network-access guarantee
    (issue #218)."""
    scratch = str(tmp_path / "scratch-credentials.json")
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        external_usage_credentials_path=scratch,
    )

    ctx = build_loop_context(config, FakeHub(), workspace_prompt="", runner_prompt="")

    assert isinstance(ctx.harness, ClaudeCodeAdapter)
    assert ctx.harness._credentials_path == scratch


@pytest.mark.unit
def test_build_loop_context_threads_runner_dir_from_the_resolved_root(tmp_path: Path) -> None:
    """The wrapped takeover command (issue #251) is composed at escalation from
    ``LoopConfig.runner_dir``, which must mirror ``RunnerConfig``'s own resolved
    ``root`` — the runtime directory a human's ``blizzard runner takeover <chunk_id>
    --dir <runner_dir>`` needs to land back in *this* runner, not some other
    composition root's idea of it."""
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
    )

    ctx = build_loop_context(config, FakeHub(), workspace_prompt="", runner_prompt="")

    assert ctx.config.runner_dir == str(config.root.resolve())


@pytest.mark.unit
def test_periodic_driver_resolves_prompts_eagerly_at_construction(tmp_path: Path) -> None:
    """A configured-but-missing ``runner_prompt_file`` must raise ``ConfigError`` from
    the constructor — on the caller's (``host``'s) own thread — not from inside the
    background loop thread it starts, where it would silently kill the loop while
    uvicorn keeps serving (issue #103's doubled prompt surface)."""
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        runner_prompt_file="does-not-exist.md",
    )

    with pytest.raises(ConfigError):
        PeriodicDriver(config, interval_seconds=30.0)
