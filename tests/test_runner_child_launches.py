"""The runner's own tool launches through the launch seam, against real children (component tier): a timed-out
check leaves no descendant behind, and the winter CLI, env-git and version-probe children carry no hub token even
when the daemon's env holds one (``bzh:worker-env-allowlist``)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.runner.environments.internal.git import SubprocessEnvGit
from blizzard.runner.environments.internal.winter_cli import SubprocessWinterCli
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.lifecycle.judgement.internal.subprocess_check_runner import SubprocessCheckRunner
from tests.support_child_launch import CHILDREN, tool_env

pytestmark = pytest.mark.component

_TOKEN_VARIABLE = hub_token_variable("r-launch")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_timed_out_check_leaves_no_grandchild_behind(tmp_path: Path) -> None:
    """A check's timeout kills its whole group, so a descendant it forked cannot outlive the red outcome."""
    pidfile = tmp_path / "grandchild.pid"
    runner = SubprocessCheckRunner(children=CHILDREN, env=tool_env())
    outcome = runner.run(f"sh -c 'sleep 900 & echo $! > {pidfile}; wait'", cwd=str(tmp_path), timeout=1)
    assert outcome.passed is False
    assert "timed out" in outcome.output_tail
    grandchild = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(grandchild)


@pytest.fixture
def env_dumping_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``PATH`` led by ``winter``, ``git`` and ``probe-tool`` stand-ins that print their own env, with the hub
    token set in the daemon's env before the tool profile is built."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("winter", "git", "probe-tool"):
        tool = bin_dir / name
        tool.write_text("#!/bin/sh\nenv\n")
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv(_TOKEN_VARIABLE, "super-secret")
    return bin_dir


def _names(dump: str) -> set[str]:
    return {line.split("=", 1)[0] for line in dump.splitlines() if "=" in line}


def test_a_winter_cli_launch_carries_no_hub_token(env_dumping_bin: Path, tmp_path: Path) -> None:
    dump = SubprocessWinterCli(children=CHILDREN, env=tool_env()).capture(tmp_path, ["status"])
    assert "PATH" in _names(dump)
    assert _TOKEN_VARIABLE not in _names(dump)


def test_an_env_git_launch_carries_no_hub_token(env_dumping_bin: Path) -> None:
    dump = SubprocessEnvGit(children=CHILDREN, env=tool_env()).capture(None, "ls-remote", "file:///nowhere")
    assert "PATH" in _names(dump)
    assert _TOKEN_VARIABLE not in _names(dump)


def test_a_version_probe_launch_carries_no_hub_token(env_dumping_bin: Path) -> None:
    dump = VersionProbe(CHILDREN, tool_env()).observe_version("probe-tool")
    assert dump is not None
    assert "PATH" in _names(dump)
    assert _TOKEN_VARIABLE not in _names(dump)
