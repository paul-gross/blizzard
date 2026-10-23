"""The runner's one launch seam for child processes (``bzh:dependency-inversion``).

Every child the runner launches goes through :class:`IChildProcesses` under one rule: it leads its own process group,
a timeout kills the group rather than just the child, stdin is closed unless input is given, and its env is a
:class:`~blizzard.runner.process.child_env.ChildEnv` profile, never the daemon's ``os.environ``. No ``subprocess``
type crosses it: results, handles, streams and the launch error are the seam's own."""

from __future__ import annotations

import enum
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.process.child_env import ChildEnv


class Stream(enum.Enum):
    """A child's standard-stream wiring other than an open file."""

    DEVNULL = enum.auto()
    PIPE = enum.auto()
    #: The runner's own stream, for a stdout or stderr the caller leaves undirected.
    INHERIT = enum.auto()
    #: Valid for stderr only: merge it into stdout.
    STDOUT = enum.auto()


type StreamTarget = Stream | IO[bytes]


class ChildLaunchError(OSError):
    """The child could not be started — its executable or its child-side setup failed before ``exec``."""


@domain_model
@dataclass(frozen=True)
class ShellCommand:
    """An authored shell string (``mise run lint``), run through ``/bin/sh`` — distinct from an argv."""

    script: str


@domain_model
@dataclass(frozen=True)
class ChildRun:
    """A run-to-completion child's result. ``exit_code`` is ``None`` when the run timed out and its group was
    killed; the output is what the child wrote before it exited or was killed."""

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


class ISpawnedChild(Protocol):
    """A long-lived child leading its own group; ``pid`` is also its pgid, stopped through the group helpers."""

    @property
    def pid(self) -> int: ...

    @property
    def stdin(self) -> IO[bytes] | None: ...

    @property
    def stdout(self) -> IO[bytes] | None: ...

    @property
    def stderr(self) -> IO[bytes] | None: ...

    def poll(self) -> int | None:
        """The exit code if the child has exited, else ``None``."""
        ...

    def wait(self, timeout: float | None) -> int | None:
        """Wait up to ``timeout`` (``None`` waits forever) for the exit code; ``None`` when it ran out."""
        ...


class IPtyChild(ISpawnedChild, Protocol):
    """A long-lived child attached to a new pseudo-terminal it leads as its session; the caller reads and writes
    ``master_fd`` and owns closing it. It has no pipe streams: its stdio is the terminal."""

    @property
    def master_fd(self) -> int: ...


class IChildProcesses(Protocol):
    """The three ways the runner launches a child."""

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
        """Run to completion within ``timeout`` seconds; a timeout kills the whole group and is a result, never a
        raise. ``input`` is written at once and stdin closed ``input_settle`` seconds later (within ``timeout``), so
        a reply still in flight when EOF would drop it can land first. A ``stdout`` file receives the child's
        stdout in place of the result's, which is then empty. Any raise while the child runs kills its group
        first. Raises :class:`ChildLaunchError` only when the child cannot be started."""
        ...

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
        """Start a long-lived child in its own group; ``child_setup`` runs in the child between fork and exec, and
        only ``pass_fds`` are inherited. Raises :class:`ChildLaunchError` when it cannot be started."""
        ...

    def spawn_pty(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str,
        env: ChildEnv,
        child_setup: Callable[[], None] | None = None,
    ) -> IPtyChild:
        """Start a long-lived child with a controlling pseudo-terminal, ``child_setup`` running in it between fork
        and exec. Raises :class:`ChildLaunchError` when it cannot be started."""
        ...

    def run_foreground(self, command: ShellCommand, *, cwd: Path | str, env: ChildEnv) -> int:
        """Run an interactive child in the operator's terminal — no new group, so it keeps the terminal's
        foreground, and no timeout — returning its exit code."""
        ...
