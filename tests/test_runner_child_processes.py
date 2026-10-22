"""The runner's child-process launch seam against its real driver: a timeout or any raise mid-run kills the whole
group, stdin is closed unless input is given, a spawned child leads its own group, a child that cannot start raises
the seam's launch error, and the tool profile carries no hub token."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.child_processes import ChildLaunchError, ShellCommand, Stream
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


def test_a_raise_while_a_run_waits_kills_its_grandchild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "gc.pid"
    real_communicate = subprocess.Popen.communicate
    raised: list[bool] = []

    def interrupted(
        self: subprocess.Popen[str], input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        if not raised:
            raised.append(True)
            deadline = time.monotonic() + 10
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            raise KeyboardInterrupt
        return real_communicate(self, input, timeout)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    script = ShellCommand(f"sleep 900 & echo $! > {pid_file}; wait")
    with pytest.raises(KeyboardInterrupt):
        SubprocessChildProcesses().run(script, cwd=tmp_path, env=_TOOL, timeout=60)
    assert _gone(int(pid_file.read_text()))


def test_a_timed_out_run_returns_though_an_escaped_descendant_holds_its_pipe(tmp_path: Path) -> None:
    pid_file = tmp_path / "escaped.pid"
    script = ShellCommand(f"setsid sleep 60 & echo $! > {pid_file}; wait")
    started = time.monotonic()
    try:
        result = SubprocessChildProcesses().run(script, cwd=tmp_path, env=_TOOL, timeout=0.5)
        assert result.timed_out
        assert time.monotonic() - started < 15
    finally:
        os.kill(int(pid_file.read_text()), signal.SIGKILL)


def test_a_missing_binary_raises_the_launch_error(tmp_path: Path) -> None:
    missing = [str(tmp_path / "no-such-binary")]
    with pytest.raises(ChildLaunchError):
        SubprocessChildProcesses().spawn(missing, cwd=tmp_path, env=_TOOL)
    with pytest.raises(ChildLaunchError):
        SubprocessChildProcesses().run(missing, cwd=tmp_path, env=_TOOL, timeout=10)


def test_binary_presence_reads_the_tool_profiles_path_not_the_daemons(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "profile-only-tool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    children = SubprocessChildProcesses()
    assert VersionProbe(children, _TOOL.adding({"PATH": str(bin_dir)})).binary_present("profile-only-tool")
    assert not VersionProbe(children, _TOOL).binary_present("profile-only-tool")


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
