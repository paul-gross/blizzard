"""The ``subprocess``-backed driver behind ``IProcessLauncher``: every adapter's worker, judge, or
resume child is launched through :class:`ProcessLauncher`, so it always gets its own group and a
parent-death signal (``bzh:deterministic-shell``). ``defer_disarm=True`` holds the real binary behind
a trampoline until ``confirm_durable()`` receives its disarm acknowledgement."""

from __future__ import annotations

import ctypes
import errno
import os
import select
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import Executor
from typing import IO

from blizzard.runner.harness.process_launch import IProcessLauncher, LaunchedProcess
from blizzard.runner.process.probe import IProcessProbe

# ``man 2 prctl`` — arms the child's own death signal (the trampoline clears it with a literal 0).
_PR_SET_PDEATHSIG = 1
_DISARM_TIMEOUT_SECONDS = 10.0

# Handle and symbol both resolved at import, never post-fork: the dynamic linker deadlocks forked children.
_LIBC = ctypes.CDLL(None, use_errno=True)
_PRCTL = _LIBC.prctl


def _die_with_parent() -> None:
    """``preexec_fn``: runs in the forked child, after ``fork()`` and before ``exec()``.
    Arms ``PR_SET_PDEATHSIG`` so a launcher crash — not a graceful exit — still reaps
    every child it owns. The "parent" ``prctl`` tracks is the OS *thread* that called it
    (this launcher, on its ``executor``'s worker — see :class:`ProcessLauncher`), unaffected
    by the child's later ``setsid()``."""
    _PRCTL(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)


# The interposed trampoline — its own tiny `ctypes` call, in its own exec'd process.
_TRAMPOLINE_SOURCE = """
import ctypes, os, sys
_libc = ctypes.CDLL(None, use_errno=True)
control_fd = int(sys.argv[1])
ack_fd = int(sys.argv[2])
argv = sys.argv[3:]
# os.read returns b"" on EOF, so it is checked explicitly: only a real confirm byte disarms
# and execs; EOF (the launcher died before confirming) exits here, still armed.
confirmed = os.read(control_fd, 1) == b"1"
os.close(control_fd)
if not confirmed:
    os._exit(1)
if _libc.prctl(1, 0, 0, 0, 0) != 0:
    os._exit(1)
# The launcher may retire its spawner thread once it reads this acknowledgement:
# the kernel has already cleared the parent-death signal on this child.
os.write(ack_fd, b"1")
os.close(ack_fd)
os.execvp(argv[0], argv)
"""


class ProcessLauncher:
    """The one production :class:`IProcessLauncher` — every adapter launches through this.
    ``executor`` is required: the composition root injects the one it owns, so both
    bindings share it."""

    def __init__(self, process: IProcessProbe, *, executor: Executor) -> None:
        self._process = process
        self._executor = executor

    def launch(
        self,
        argv: list[str],
        *,
        cwd: str | None,
        env: dict[str, str],
        stdout: IO[bytes] | int | None,
        stderr: IO[bytes] | int | None,
        defer_disarm: bool = False,
    ) -> LaunchedProcess:
        if not defer_disarm:
            return self._launch(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
        # The deferred `exec()` runs inside the trampoline, so `Popen`'s own errpipe never
        # sees a bad `argv[0]` — reproduce that one guarantee synchronously, up front.
        _ensure_executable(argv[0], cwd=cwd, env=env)
        # `pass_fds` inherits only the trampoline's own read end — `argv` never sees it.
        read_fd, write_fd = os.pipe()
        ack_read_fd, ack_write_fd = os.pipe()
        try:
            try:
                proc = self._launch_process(
                    [sys.executable, "-c", _TRAMPOLINE_SOURCE, str(read_fd), str(ack_write_fd), *argv],
                    cwd=cwd,
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    pass_fds=(read_fd, ack_write_fd),
                )
            finally:
                os.close(read_fd)  # the child's copies survive the fork
                os.close(ack_write_fd)
        except BaseException:
            os.close(write_fd)
            os.close(ack_read_fd)
            raise
        start_time = self._process.start_time(proc.pid) or ""
        return LaunchedProcess(
            pid=proc.pid,
            pgid=proc.pid,
            process_start_time=start_time,
            confirm_durable=_confirm_once(write_fd, ack_read_fd),
        )

    def _launch(
        self,
        argv: list[str],
        *,
        cwd: str | None,
        env: dict[str, str],
        stdout: IO[bytes] | int | None,
        stderr: IO[bytes] | int | None,
    ) -> LaunchedProcess:
        """The plain, non-deferred launch: the real binary directly, armed for its whole life —
        the right one for a caller with no durable-record milestone of its own to defer a
        disarm to. Every adapter launch defers now (spawn, judge, and resume all have one to
        defer to); this stays the base case for whatever narrower caller genuinely has none."""
        proc = self._launch_process(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
        start_time = self._process.start_time(proc.pid) or ""
        return LaunchedProcess(pid=proc.pid, pgid=proc.pid, process_start_time=start_time, confirm_durable=lambda: None)

    def _launch_process(
        self,
        argv: list[str],
        *,
        cwd: str | None,
        env: dict[str, str],
        stdout: IO[bytes] | int | None,
        stderr: IO[bytes] | int | None,
        pass_fds: tuple[int, ...] = (),
    ) -> subprocess.Popen[bytes]:
        # fork()/exec() runs on `self._executor`'s worker thread, not the caller's;
        # this call blocks for it, so the caller's own timing is unchanged.
        return self._executor.submit(
            subprocess.Popen,  # argv is adapter-composed, never shell-interpreted
            argv,
            cwd=cwd,
            env=env,
            # pinned by `test_a_worker_never_inherits_the_daemons_open_stdin`
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            preexec_fn=_die_with_parent,
            pass_fds=pass_fds,
        ).result()


def _ensure_executable(argv0: str, *, cwd: str | None, env: dict[str, str]) -> None:
    """Raise exactly where ``execvp(argv0, ...)`` would later fail inside the trampoline:
    a path-shaped ``argv0`` resolves relative to ``cwd``; a bare name searches the child's
    own ``PATH``, never the launcher's ambient one. Missing raises :class:`FileNotFoundError`
    (``ENOENT``); existing-but-not-executable raises :class:`PermissionError` (``EACCES``)
    instead, matching real ``execvp``."""
    if os.sep in argv0:
        candidate = argv0 if os.path.isabs(argv0) else os.path.join(cwd or os.getcwd(), argv0)
        if os.path.isfile(candidate):
            if os.access(candidate, os.X_OK):
                return
            raise PermissionError(errno.EACCES, "Permission denied", argv0)
    elif shutil.which(argv0, path=env.get("PATH")) is not None:
        return
    raise FileNotFoundError(errno.ENOENT, "No such file or directory", argv0)


def _confirm_once(write_fd: int, ack_read_fd: int) -> Callable[[], None]:
    """One single-use disarm closure per launch: writes the trampoline's go-byte, then
    closes the write end, and waits for the child's disarm acknowledgement before the
    spawner thread may be retired. A second call is a harmless no-op."""
    sent = False

    def confirm_durable() -> None:
        nonlocal sent
        if sent:
            return
        sent = True
        try:
            os.write(write_fd, b"1")
        except OSError:
            pass  # the trampoline (or its exec'd successor) is already gone — nothing to tell
        finally:
            os.close(write_fd)
        try:
            ready, _, _ = select.select([ack_read_fd], [], [], _DISARM_TIMEOUT_SECONDS)
            if not ready:
                raise TimeoutError("worker trampoline did not confirm parent-death disarm")
            # EOF means the child exited before disarm; it cannot be killed by
            # retirement of the spawner thread. A live child sends b"1".
            os.read(ack_read_fd, 1)
        finally:
            os.close(ack_read_fd)

    return confirm_durable


def _conforms_process_launcher(x: ProcessLauncher) -> IProcessLauncher:
    return x
