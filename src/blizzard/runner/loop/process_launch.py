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
from dataclasses import dataclass
from typing import IO, Protocol

from blizzard.runner.loop.process import IProcessProbe

# ``man 2 prctl`` — arms the child's own death signal.
_PR_SET_PDEATHSIG = 1


def _die_with_parent() -> None:
    """``preexec_fn``: runs in the forked child, after ``fork()`` and before ``exec()``.

    Arms ``PR_SET_PDEATHSIG`` so a launcher crash — not a graceful exit — still reaps
    every child it owns, closing the "runner died, worker orphaned forever" gap a bare
    pid-tracked subprocess leaves open. The "parent" ``prctl`` tracks is the process that
    called it (this launcher), unaffected by the same child's own later ``setsid()``."""
    ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)


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
    """The one production :class:`IProcessLauncher` — every adapter launches through this."""

    def __init__(self, process: IProcessProbe) -> None:
        self._process = process

    def launch(
        self,
        argv: list[str],
        *,
        cwd: str | None,
        env: dict[str, str],
        stdout: IO[bytes] | int | None,
        stderr: IO[bytes] | int | None,
    ) -> LaunchedProcess:
        proc = subprocess.Popen(  # argv is adapter-composed, never shell-interpreted
            argv,
            cwd=cwd,
            env=env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            preexec_fn=_die_with_parent,
        )
        start_time = self._process.start_time(proc.pid) or ""
        return LaunchedProcess(pid=proc.pid, pgid=proc.pid, process_start_time=start_time)


def _conforms_process_launcher(x: ProcessLauncher) -> IProcessLauncher:
    return x
