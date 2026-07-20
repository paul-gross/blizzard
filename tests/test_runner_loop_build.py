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
