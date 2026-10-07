"""The seam for the one runner-owned launch of a worker, judge, or resume child: every adapter's
``spawn``/``resume_with_message``/``judge`` goes through :class:`IProcessLauncher`, never a bare
``subprocess.Popen``, so a child always gets its own group and a parent-death signal
(``bzh:deterministic-shell``). ``defer_disarm=True`` holds the real binary behind a
trampoline until ``confirm_durable()`` receives its disarm acknowledgement. The driver is
``internal/process_launcher.py``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Protocol

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class LaunchedProcess:
    """The OS facts known the instant a child exists — before any identity is known.
    ``pgid`` is recorded, not inferred at kill time: ``start_new_session=True`` makes
    the child a fresh session-and-group leader, so its pgid equals its own pid.
    ``confirm_durable`` waits for the trampoline to disarm — a no-op unless
    ``defer_disarm=True``."""

    pid: int
    pgid: int
    process_start_time: str
    confirm_durable: Callable[[], None]


class IProcessLauncher(Protocol):
    """Launches a worker, judge, or resume child under its own process group with a
    parent-death signal — the one seam every adapter launches a subprocess through."""

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
        ``HarnessSpawnError``. ``defer_disarm=True`` holds the real binary's ``exec()``
        behind a trampoline until the returned handle's ``confirm_durable()`` is called."""
        ...
