"""The runner's child-process launch seam against its real driver: a timeout kills the whole group, stdin is closed
unless input is given, a spawned child leads its own group, and the tool profile carries no hub token."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.config import RunnerConfig
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.child_processes import ShellCommand, Stream
from blizzard.runner.process.groups import group_exists, signal_group
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses

pytestmark = pytest.mark.component

_TOOL = AllowlistedEnv.of(()).tool()


def _gone(pid: int, *, within: float = 10.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_a_timed_out_run_kills_its_grandchild(tmp_path: Path) -> None:
    pid_file = tmp_path / "gc.pid"
    script = ShellCommand(f"sleep 900 & echo $! > {pid_file}; wait")
    result = SubprocessChildProcesses().run(script, cwd=tmp_path, env=_TOOL, timeout=0.5)
    assert result.timed_out
    assert result.exit_code is None
    assert _gone(int(pid_file.read_text()))


def test_a_run_without_input_sees_its_stdin_closed(tmp_path: Path) -> None:
    argv = [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"]
    result = SubprocessChildProcesses().run(argv, cwd=tmp_path, env=_TOOL, timeout=10)
    assert (result.exit_code, result.stdout.strip()) == (0, "''")


def test_a_run_with_input_reads_it(tmp_path: Path) -> None:
    argv = [sys.executable, "-c", "import sys; print(sys.stdin.read())"]
    result = SubprocessChildProcesses().run(argv, cwd=tmp_path, env=_TOOL, timeout=10, input="hello")
    assert result.stdout.strip() == "hello"


def test_a_spawned_child_leads_its_own_group(tmp_path: Path) -> None:
    argv = [sys.executable, "-c", "import os; print(os.getpgrp())"]
    child = SubprocessChildProcesses().spawn(argv, cwd=tmp_path, env=_TOOL, stdout=Stream.PIPE)
    try:
        assert child.wait(10) == 0
        assert child.stdout is not None
        assert int(child.stdout.read()) == child.pid != os.getpgrp()
    finally:
        signal_group(child.pid, signal.SIGKILL)
    assert not group_exists(child.pid)


def test_a_tool_profile_launch_carries_no_hub_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = hub_token_variable("r-test")
    monkeypatch.setenv(token, "secret")
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url="", worker_env_passthrough=("MY_QUIRK",))
    env = config.tool_env(TracingSettings("disabled"))
    result = SubprocessChildProcesses().run(["env"], cwd=tmp_path, env=env, timeout=10)
    assert result.exit_code == 0
    assert token not in result.stdout
