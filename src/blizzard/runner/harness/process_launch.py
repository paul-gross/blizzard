"""The one runner-owned launch of a worker, judge, or resume child (D4): every adapter's
``spawn``/``resume_with_message``/``judge`` goes through :class:`ProcessLauncher`, never a bare
``subprocess.Popen``, so a child always gets its own group and a parent-death signal
(``bzh:deterministic-shell``). ``defer_disarm=True`` (F1) holds the real binary behind a
trampoline until ``confirm_durable()`` disarms it."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from typing import IO, Protocol

from blizzard.runner.loop.process import IProcessProbe

# ``man 2 prctl`` — arms the child's own death signal (the trampoline clears it with a literal 0).
_PR_SET_PDEATHSIG = 1

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


# A no-DI-friction test default (`bzh:dependency-injection`) — the composition root injects its own.
_SPAWN_EXECUTOR: Executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")

# The interposed trampoline (F1, module docstring) — its own tiny `ctypes` call, in its own exec'd process.
_TRAMPOLINE_SOURCE = """
import ctypes, os, sys
_libc = ctypes.CDLL(None, use_errno=True)
control_fd = int(sys.argv[1])
argv = sys.argv[2:]
# os.read returns b"" on EOF rather than raising, so it is checked explicitly: only a
# real confirm byte disarms and execs; EOF (the launcher died before confirming) exits
# here, still armed, rather than racing PR_SET_PDEATHSIG's own SIGKILL to decide it.
confirmed = os.read(control_fd, 1) == b"1"
os.close(control_fd)
if not confirmed:
    os._exit(1)
_libc.prctl(1, 0, 0, 0, 0)
os.execvp(argv[0], argv)
"""


@dataclass(frozen=True)
class LaunchedProcess:
    """The OS facts known the instant a child exists (D1) — before any identity is known.
    ``pgid`` is recorded, not inferred at kill time (D3): ``start_new_session=True`` makes
    the child a fresh session-and-group leader, so its pgid equals its own pid.
    ``confirm_durable`` is F1's disarm signal — a no-op unless ``defer_disarm=True``."""

    pid: int
    pgid: int
    process_start_time: str
    confirm_durable: Callable[[], None]


class IProcessLauncher(Protocol):
    """Launches a worker, judge, or resume child under its own process group with a
    parent-death signal (D4) — the one seam every adapter launches a subprocess through."""

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
        """Start ``argv`` under its own process group and a parent-death signal. ``cwd``/
        ``stdout``/``stderr`` of ``None`` inherit bare ``subprocess.Popen``'s own defaults;
        stdin is always ``/dev/null``, never the launcher's own, so a child that drains stdin
        before its turn sees EOF at once.
        Raises ``OSError`` on a launch failure — each adapter translates it into its own
        ``HarnessSpawnError``. ``defer_disarm=True`` (F1) holds the real binary's ``exec()``
        behind a trampoline until the returned handle's ``confirm_durable()`` is called."""
        ...


class ProcessLauncher:
    """The one production :class:`IProcessLauncher` — every adapter launches through this.
    ``executor`` defaults to :data:`_SPAWN_EXECUTOR` for a test that doesn't care to wire
    one; the composition root (D9) always injects its own instead, so both bindings share
    ONE executor without depending on this module-level default."""

    def __init__(self, process: IProcessProbe, *, executor: Executor | None = None) -> None:
        self._process = process
        self._executor = executor if executor is not None else _SPAWN_EXECUTOR

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
        try:
            proc = self._launch_process(
                [sys.executable, "-c", _TRAMPOLINE_SOURCE, str(read_fd), *argv],
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=stderr,
                pass_fds=(read_fd,),
            )
        finally:
            os.close(read_fd)  # the parent's own copy; the child kept its own across the fork
        start_time = self._process.start_time(proc.pid) or ""
        return LaunchedProcess(
            pid=proc.pid, pgid=proc.pid, process_start_time=start_time, confirm_durable=_confirm_once(write_fd)
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
        exactly today's pre-F1 behavior, and the right one for a caller with no durable-record
        milestone of its own to defer a disarm to. Every adapter launch defers now (spawn,
        judge, and resume all have one to defer to — D1/D4); this stays the base case for
        whatever narrower caller genuinely has none."""
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
        # fork()/exec() runs on `self._executor`'s worker thread, not the caller's (D4, see
        # `_SPAWN_EXECUTOR`); this call blocks for it, so the caller's own timing is unchanged.
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


def _confirm_once(write_fd: int) -> Callable[[], None]:
    """One single-use disarm closure per launch (F1): writes the trampoline's go-byte, then
    closes the write end — a second call is a harmless no-op rather than a write against a
    possibly-reused fd number."""
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

    return confirm_durable


def _conforms_process_launcher(x: ProcessLauncher) -> IProcessLauncher:
    return x
