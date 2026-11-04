"""The proof's process work through the runner's launch seam: commands under the tool profile, the operator's TUI on
a pseudo-terminal for takeover, and a liveness check for a tool's process."""

from __future__ import annotations

import errno
import os
import select
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from blizzard.runner.harness.opencode_v2.compatibility.host import CommandResult, IProofHost
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.groups import terminate_group

#: How long the TUI is given to draw before the attended prompt is typed.
TUI_START_SECONDS = 6.0
_TERMINAL_ROWS, _TERMINAL_COLUMNS = 40, 120


def isolated_home(env: ChildEnv, home: Path) -> ChildEnv:
    """``env`` with every OpenCode directory rooted under ``home``, so nothing reads or writes the operator's."""
    return env.adding(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "XDG_CACHE_HOME": str(home / ".cache"),
        }
    )


def _drain(fd: int, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.2))
        if not ready:
            continue
        try:
            if not os.read(fd, 65536):
                return
        except OSError as exc:
            if exc.errno in {errno.EIO, errno.EBADF}:
                return
            raise


def _size_terminal(fd: int) -> None:
    import fcntl
    import struct
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", _TERMINAL_ROWS, _TERMINAL_COLUMNS, 0, 0))


@dataclass(frozen=True)
class SubprocessProofHost:
    children: IChildProcesses
    tool_env: ChildEnv

    def run(self, argv: Sequence[str], *, cwd: Path, timeout: float, home: Path | None = None) -> CommandResult:
        env = self.tool_env if home is None else isolated_home(self.tool_env, home)
        result = self.children.run(list(argv), cwd=cwd, env=env, timeout=timeout)
        return CommandResult(result.exit_code, result.stdout, result.stderr)

    def attend(self, argv: Sequence[str], *, cwd: Path, keystrokes: str, settle_seconds: float) -> None:
        env = self.tool_env.adding({"TERM": "xterm-256color"})
        child = self.children.spawn_pty(list(argv), cwd=cwd, env=env)
        fd = child.master_fd
        try:
            _size_terminal(fd)
            _drain(fd, TUI_START_SECONDS)
            for character in keystrokes:
                os.write(fd, character.encode())
                time.sleep(0.02)
            _drain(fd, settle_seconds)
        finally:
            terminate_group(child.pid, grace=2.0, wait=child.wait)
            os.close(fd)

    def process_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


_host_conforms: type[IProofHost] = SubprocessProofHost

__all__ = ["SubprocessProofHost", "isolated_home"]
