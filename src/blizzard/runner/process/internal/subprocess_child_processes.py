"""The ``subprocess``/``pty`` driver behind :class:`~blizzard.runner.process.child_processes.IChildProcesses` — the
only runner module outside the group helpers that touches either."""

from __future__ import annotations

import os
import pty
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO

from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import (
    ChildLaunchError,
    ChildRun,
    IChildProcesses,
    ISpawnedChild,
    PtyChild,
    ShellCommand,
    Stream,
    StreamTarget,
)
from blizzard.runner.process.groups import terminate_group

# A timed-out child's group gets this long after SIGTERM before SIGKILL.
_TIMEOUT_GRACE_SECONDS = 2.0

_STREAMS = {Stream.DEVNULL: subprocess.DEVNULL, Stream.PIPE: subprocess.PIPE, Stream.STDOUT: subprocess.STDOUT}


def _target(stream: StreamTarget) -> int | IO[bytes]:
    return _STREAMS[stream] if isinstance(stream, Stream) else stream


class _PopenChild:
    """:class:`ISpawnedChild` over a ``Popen``, translating its timeout into ``None``."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def stdin(self) -> IO[bytes] | None:
        return self._process.stdin

    @property
    def stdout(self) -> IO[bytes] | None:
        return self._process.stdout

    @property
    def stderr(self) -> IO[bytes] | None:
        return self._process.stderr

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None) -> int | None:
        try:
            return self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None


class SubprocessChildProcesses:
    """Every launch starts a new session, so the child's pid is its pgid and a group kill reaches its descendants."""

    def run(
        self,
        command: Sequence[str] | ShellCommand,
        *,
        cwd: Path | str | None,
        env: ChildEnv,
        timeout: float,
        input: str | None = None,
    ) -> ChildRun:
        process = subprocess.Popen(
            command.script if isinstance(command, ShellCommand) else list(command),
            shell=isinstance(command, ShellCommand),
            cwd=cwd,
            env=env.variables,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            output = terminate_group(
                process.pid, grace=_TIMEOUT_GRACE_SECONDS, wait=lambda grace: process.communicate(timeout=grace)
            )
            stdout, stderr = output if output is not None else process.communicate()
            return ChildRun(exit_code=None, stdout=stdout, stderr=stderr, timed_out=True)
        return ChildRun(exit_code=process.returncode, stdout=stdout, stderr=stderr, timed_out=False)

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None,
        env: ChildEnv,
        stdin: StreamTarget = Stream.DEVNULL,
        stdout: StreamTarget = Stream.DEVNULL,
        stderr: StreamTarget = Stream.DEVNULL,
        child_setup: Callable[[], None] | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ISpawnedChild:
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=env.variables,
                stdin=_target(stdin),
                stdout=_target(stdout),
                stderr=_target(stderr),
                start_new_session=True,
                preexec_fn=child_setup,
                pass_fds=pass_fds,
            )
        except subprocess.SubprocessError as exc:
            raise ChildLaunchError(str(exc)) from exc
        return _PopenChild(process)

    def spawn_pty(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str,
        env: ChildEnv,
        child_setup: Callable[[], None] | None = None,
    ) -> PtyChild:
        variables = env.variables
        child_pid, master_fd = pty.fork()
        if child_pid == 0:
            try:
                os.chdir(cwd)
                if child_setup is not None:
                    child_setup()
                os.execvpe(str(argv[0]), list(argv), variables)
            except BaseException:
                os._exit(127)
        return PtyChild(pid=child_pid, master_fd=master_fd)

    def run_foreground(self, command: ShellCommand, *, cwd: Path | str, env: ChildEnv) -> int:
        return subprocess.call(command.script, shell=True, cwd=cwd, env=env.variables)


def _conforms_child_processes(x: SubprocessChildProcesses) -> IChildProcesses:
    return x
