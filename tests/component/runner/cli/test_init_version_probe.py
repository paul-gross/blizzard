"""``blizzard runner init`` chooses each version-chosen binding by running its binary's ``--version`` through the
invoker carrier's launcher, never one it builds for itself."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import IO

import click

from blizzard.cli.collaborators import CliCollaborators
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.children import RunnerCliCollaborators
from blizzard.runner.host_layout import HOST_CONFIG_FILENAME
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import ChildRun, ShellCommand
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses
from tests.support.runner_init_fakes import FakeInitHub, init_runner


class _RecordingChildren(SubprocessChildProcesses):
    """Answers every run-to-completion child with a version line, recording its command, and launches nothing."""

    def __init__(self) -> None:
        self.runs: list[list[str]] = []

    def run(
        self,
        command: Sequence[str] | ShellCommand,
        *,
        cwd: Path | str | None,
        env: ChildEnv,
        timeout: float,
        input: str | None = None,
        input_settle: float = 0.0,
        stdout: IO[bytes] | None = None,
    ) -> ChildRun:
        assert not isinstance(command, ShellCommand)
        self.runs.append(list(command))
        return ChildRun(exit_code=0, stdout="1.0.0\n", stderr="", timed_out=False)


def test_init_probes_each_version_chosen_binary_through_the_carriers_launcher(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    runtime = tmp_path / "runner"
    assert init_runner(fake_init_hub).invoke(runner_group, ["init", str(runtime)]).exit_code == 0
    opencode = tmp_path / "opencode"
    opencode.write_text("#!/bin/sh\nexit 1\n")
    opencode.chmod(0o755)
    path = runtime / HOST_CONFIG_FILENAME
    path.write_text(path.read_text().replace('binary = "opencode"\n', f'binary = "{opencode}"\n', 1))
    children = _RecordingChildren()
    carrier = replace(RunnerCliCollaborators.extending(CliCollaborators.production()), children=children)

    # The init collaborators ride as the invocation's ``obj``; the carrier sits on the context above it.
    result = init_runner(fake_init_hub).invoke(
        runner_group, ["init", str(runtime)], parent=click.Context(runner_group, obj=carrier)
    )

    assert result.exit_code == 0, result.output
    assert children.runs == [[str(opencode), "--version"]]
