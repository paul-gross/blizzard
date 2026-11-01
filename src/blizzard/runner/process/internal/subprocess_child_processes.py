"""The ``subprocess`` driver behind :class:`~blizzard.runner.process.child_processes.IChildProcesses` — the
only runner module outside the group helpers that touches it."""

from __future__ import annotations

import fcntl
import os
import subprocess
import termios
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO

from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import (
    ChildLaunchError,
    ChildRun,
    IChildProcesses,
    IPtyChild,
    ISpawnedChild,
    ShellCommand,
    Stream,
    StreamTarget,
)
from blizzard.runner.process.groups import terminate_group

# A timed-out child's group gets this long after SIGTERM before SIGKILL.
_TIMEOUT_GRACE_SECONDS = 2.0
_DRAIN_SECONDS = 1.0

_STREAMS = {
    Stream.DEVNULL: subprocess.DEVNULL,
    Stream.PIPE: subprocess.PIPE,
    Stream.STDOUT: subprocess.STDOUT,
    Stream.INHERIT: None,
}


def _target(stream: StreamTarget) -> int | IO[bytes] | None:
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


class _PtyPopenChild(_PopenChild):
    """:class:`IPtyChild`: a ``Popen`` whose stdio is the pseudo-terminal's slave, so the master is its only channel."""

    def __init__(self, process: subprocess.Popen[bytes], master_fd: int) -> None:
        super().__init__(process)
        self._master_fd = master_fd

    @property
    def master_fd(self) -> int:
        return self._master_fd


def _claim_controlling_terminal(child_setup: Callable[[], None] | None) -> Callable[[], None]:
    """The child-side setup of a pty child: the slave it holds as stdin becomes its session's controlling terminal,
    then ``child_setup`` runs."""

    def setup() -> None:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        if child_setup is not None:
            child_setup()

    return setup


def write_unclosed(process: subprocess.Popen[str], text: str) -> None:
    """Write ``text`` to the child's stdin and leave it open; a child that already exited or closed its own stdin
    is not an error. ``text`` is small enough to fit the pipe buffer with no concurrent reader."""
    assert process.stdin is not None
    try:
        process.stdin.write(text)
        process.stdin.flush()
    except OSError:
        pass


def _communicated(process: subprocess.Popen[str]) -> Callable[[float], tuple[str, str] | None]:
    """The grace wait :func:`terminate_group` bounds: the child's output once it exits, ``None`` when it ran out."""

    def wait(grace: float) -> tuple[str, str] | None:
        try:
            return process.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            return None

    return wait


def _killed(process: subprocess.Popen[str]) -> tuple[str, str]:
    """Kill the child's group and collect its output; the drain is bounded, as a descendant may hold a pipe open."""
    output = terminate_group(process.pid, grace=_TIMEOUT_GRACE_SECONDS, wait=_communicated(process))
    if output is None:
        output = _communicated(process)(_DRAIN_SECONDS)
    if output is None:
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        process.wait()
        return "", ""
    out, err = output
    return out or "", err or ""


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
        input_settle: float = 0.0,
        stdout: IO[bytes] | None = None,
    ) -> ChildRun:
        deadline = time.monotonic() + timeout
        try:
            process = subprocess.Popen(
                command.script if isinstance(command, ShellCommand) else list(command),
                shell=isinstance(command, ShellCommand),
                cwd=cwd,
                env=env.variables,
                stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE if stdout is None else stdout,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ChildLaunchError(str(exc)) from exc
        try:
            if input is not None and input_settle > 0:
                write_unclosed(process, input)
                time.sleep(max(0.0, min(input_settle, deadline - time.monotonic())))
                input = None
            out, err = process.communicate(input, timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            out, err = _killed(process)
            return ChildRun(exit_code=None, stdout=out, stderr=err, timed_out=True)
        except BaseException:
            _killed(process)
            raise
        return ChildRun(exit_code=process.returncode, stdout=out or "", stderr=err or "", timed_out=False)

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
        except (OSError, subprocess.SubprocessError) as exc:
            raise ChildLaunchError(str(exc)) from exc
        return _PopenChild(process)

    def spawn_pty(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str,
        env: ChildEnv,
        child_setup: Callable[[], None] | None = None,
    ) -> IPtyChild:
        master_fd, slave_fd = os.openpty()
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=env.variables,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                preexec_fn=_claim_controlling_terminal(child_setup),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            os.close(master_fd)
            raise ChildLaunchError(str(exc)) from exc
        finally:
            os.close(slave_fd)
        return _PtyPopenChild(process, master_fd)

    def run_foreground(self, command: ShellCommand, *, cwd: Path | str, env: ChildEnv) -> int:
        return subprocess.call(command.script, shell=True, cwd=cwd, env=env.variables)


_child_processes_conforms: type[IChildProcesses] = SubprocessChildProcesses
