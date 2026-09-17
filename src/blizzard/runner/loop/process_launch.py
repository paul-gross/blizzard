"""The one runner-owned launch of a worker, judge, or resume child (D4).

Mirrors ``AllowlistedEnv``'s "one place decides" shape for process *ownership* rather than
environment: every adapter's ``spawn``/``resume_with_message``/``judge`` launches its
subprocess through :class:`ProcessLauncher`, never a bare ``subprocess.Popen`` of its own,
so a launched child always gets its own process group and a parent-death signal — an
adapter translates a harness command into argv/env; only this module decides how the OS
process comes to exist (``bzh:deterministic-shell``)."""

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

# Resolved once, at import time — well before any fork ever happens, never inside the
# post-fork hook below. ``ctypes.CDLL`` is a ``dlopen(3)`` call, and CPython's own docs
# name post-fork ``dlopen`` a deadlock hazard in a multithreaded process: it may need a
# libc-internal lock some OTHER thread held at fork time, and only the forking thread
# survives fork() in the child, so that lock is never released there — the child wedges
# forever, holding a provisional lease on every launch. Reading the handle here means
# ``_die_with_parent`` only ever touches an already-open one, after fork, dlopen-free.
_LIBC = ctypes.CDLL(None, use_errno=True)


def _die_with_parent() -> None:
    """``preexec_fn``: runs in the forked child, after ``fork()`` and before ``exec()``.

    Arms ``PR_SET_PDEATHSIG`` so a launcher crash — not a graceful exit — still reaps
    every child it owns, closing the "runner died, worker orphaned forever" gap a bare
    pid-tracked subprocess leaves open. The "parent" ``prctl`` tracks is the OS *thread*
    that called it (this launcher, on :data:`_SPAWN_EXECUTOR`'s own worker — see its own
    docstring for why that thread, specifically), unaffected by the same child's own
    later ``setsid()``."""
    _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)


# The one thread every launch's actual ``fork()``/``exec()`` runs on (D4), created once
# here at module import — during composition, well before the daemon's tick loop starts —
# and never torn down short of full process exit. ``PR_SET_PDEATHSIG`` tracks the
# SPECIFIC OS thread that called it, not the daemon process (Linux-specific, thread-
# scoped — a longstanding, well-documented ``prctl`` surprise): a caller's own thread
# (the reconciliation loop's tick thread, stopped by ``PeriodicDriver.stop()`` on every
# graceful shutdown while the daemon process keeps serving) is not pinned to the
# process's full lifetime, so forking directly from it would SIGKILL every
# already-launched, healthily-running worker the instant that thread — not the daemon —
# exits. Proxying the syscall through this single long-lived worker keeps the tracked
# "parent" alive across a graceful ``driver.stop()``, so only the daemon process's own
# death (crash or true exit) can ever trip the child's parent-death signal.
_SPAWN_EXECUTOR: Executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")


@dataclass(frozen=True)
class LaunchedProcess:
    """The OS facts known the instant a child exists (D1) — before any identity is known.

    ``pgid`` is recorded, not inferred at kill time (D3): ``start_new_session=True`` makes
    the child a fresh session-and-group leader, so its pgid equals its own pid by the
    POSIX ``setsid()`` contract at the moment this launch created it — read here, once,
    rather than re-derived later from a bare pid a recovery pass only assumes equals it."""

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
        """Start ``argv`` under its own process group and a parent-death signal.

        ``cwd`` of ``None`` inherits the launcher's own cwd, matching bare
        ``subprocess.Popen``'s own default. ``stdout``/``stderr`` of ``None`` likewise
        inherit the launcher's own — never silently redirected to ``DEVNULL``.

        Raises ``OSError`` on a launch failure (missing binary, bad ``cwd``) — the same
        exception a bare ``subprocess.Popen`` raises; each adapter translates it into its
        own ``HarnessSpawnError``, never this harness-neutral seam."""
        ...


class ProcessLauncher:
    """The one production :class:`IProcessLauncher` — every adapter launches through this.

    ``executor`` defaults to the module's own long-lived :data:`_SPAWN_EXECUTOR` (see its
    docstring); a test may inject its own single-worker executor so it can shut down that
    ONE worker thread in isolation — simulating a graceful ``driver.stop()`` — without
    reaching into the shared production singleton every other launcher call uses."""

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
        # The actual fork()/exec() runs on `self._executor`'s own worker thread (D4), never
        # on the calling thread directly — see `_SPAWN_EXECUTOR`'s docstring for why. This
        # call blocks until that thread finishes it, so the caller's own timing is unchanged.
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
