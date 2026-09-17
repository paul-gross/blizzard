"""The one runner-owned launch of a worker, judge, or resume child (D4).

Mirrors ``AllowlistedEnv``'s "one place decides" shape for process *ownership*: every
adapter's ``spawn``/``resume_with_message``/``judge`` launches its subprocess through
:class:`ProcessLauncher`, never a bare ``subprocess.Popen``, so a child always gets its own
process group and a parent-death signal (``bzh:deterministic-shell``)."""

from __future__ import annotations

import ctypes
import signal
import subprocess
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from typing import IO, Protocol

from blizzard.runner.loop.process import IProcessProbe

# ``man 2 prctl`` — arms the child's own death signal.
_PR_SET_PDEATHSIG = 1

# Handle and symbol both resolved at import, never post-fork: the dynamic linker deadlocks forked children.
_LIBC = ctypes.CDLL(None, use_errno=True)
_PRCTL = _LIBC.prctl


def _die_with_parent() -> None:
    """``preexec_fn``: runs in the forked child, after ``fork()`` and before ``exec()``.
    Arms ``PR_SET_PDEATHSIG`` so a launcher crash — not a graceful exit — still reaps
    every child it owns. The "parent" ``prctl`` tracks is the OS *thread* that called it
    (this launcher, on :data:`_SPAWN_EXECUTOR`'s worker — see its docstring), unaffected
    by the child's later ``setsid()``."""
    _PRCTL(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)


# The one long-lived thread every launch forks on (D4): PDEATHSIG tracks the calling thread, not the daemon.
_SPAWN_EXECUTOR: Executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")


@dataclass(frozen=True)
class LaunchedProcess:
    """The OS facts known the instant a child exists (D1) — before any identity is known.
    ``pgid`` is recorded, not inferred at kill time (D3): ``start_new_session=True`` makes
    the child a fresh session-and-group leader, so its pgid equals its own pid by POSIX
    ``setsid()`` — read here, once, not re-derived later from an assumed-equal pid."""

    pid: int
    pgid: int
    process_start_time: str


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
    ) -> LaunchedProcess:
        """Start ``argv`` under its own process group and a parent-death signal. ``cwd`` of
        ``None`` inherits the launcher's cwd, matching bare ``subprocess.Popen``'s default;
        same for ``stdout``/``stderr``, never silently redirected to ``DEVNULL``. Raises
        ``OSError`` on a launch failure — each adapter translates it into its own
        ``HarnessSpawnError``, never this harness-neutral seam."""
        ...


class ProcessLauncher:
    """The one production :class:`IProcessLauncher` — every adapter launches through this.
    ``executor`` defaults to the module's long-lived :data:`_SPAWN_EXECUTOR`; a test may
    inject its own single-worker executor to shut it down in isolation, without touching
    the shared production singleton."""

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
    ) -> LaunchedProcess:
        # fork()/exec() runs on `self._executor`'s worker thread, not the caller's (D4, see
        # `_SPAWN_EXECUTOR`); this call blocks for it, so the caller's own timing is unchanged.
        proc = self._executor.submit(
            subprocess.Popen,  # argv is adapter-composed, never shell-interpreted
            argv,
            cwd=cwd,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            preexec_fn=_die_with_parent,
        ).result()
        start_time = self._process.start_time(proc.pid) or ""
        return LaunchedProcess(pid=proc.pid, pgid=proc.pid, process_start_time=start_time)


def _conforms_process_launcher(x: ProcessLauncher) -> IProcessLauncher:
    return x
