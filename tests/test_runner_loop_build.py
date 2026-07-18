"""``build_loop_context`` wiring (``bzh:dependency-injection``) — issue #88.

The composition root threads ``RunnerConfig.worker_env_passthrough`` into the
``ClaudeCodeAdapter`` it constructs, so the operator's ``[worker] env_passthrough``
toml key actually reaches the spawn-environment allowlist rather than being read and
dropped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.loop.build import build_loop_context
from tests.runner_fakes import FakeHub


@pytest.mark.unit
def test_build_loop_context_threads_worker_env_passthrough_into_the_adapter(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        workspace_root=str(tmp_path / "workspace"),
        worker_env_passthrough=("MY_HARNESS_QUIRK", "ANOTHER_VAR"),
    )

    ctx = build_loop_context(config, FakeHub())

    assert isinstance(ctx.harness, ClaudeCodeAdapter)
    assert ctx.harness._env_passthrough == ("MY_HARNESS_QUIRK", "ANOTHER_VAR")
