"""Process-liveness by (pid, start_time) — the reuse-proof reap signal.

Exercised against real OS processes: the current process is alive at its own start
time, a never-existent pid is dead, and a mismatched start time (the pid-reuse case)
reads as dead even when the pid is live.
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from blizzard.foundation.process import is_zombie, read_process_start_time
from blizzard.runner.loop.process import LinuxProcessProbe


@pytest.mark.unit
def test_current_process_is_alive_at_its_own_start_time() -> None:
    probe = LinuxProcessProbe()
    pid = os.getpid()
    start = probe.start_time(pid)
    assert start is not None
    assert probe.is_alive(pid, start)


@pytest.mark.unit
def test_absent_pid_is_dead() -> None:
    probe = LinuxProcessProbe()
    # A pid that cannot be running (max+something); start_time is None -> dead.
    assert read_process_start_time(2**31 - 1) is None
    assert not probe.is_alive(2**31 - 1, "whatever")


@pytest.mark.unit
def test_mismatched_start_time_reads_dead_across_pid_reuse() -> None:
    probe = LinuxProcessProbe()
    pid = os.getpid()  # a live pid, but with the wrong recorded start time
    assert not probe.is_alive(pid, "not-the-real-start-time")


@pytest.mark.unit
def test_exited_but_unreaped_worker_reads_dead() -> None:
    """A fire-and-forget child that exited but is not yet wait()ed is a zombie -> dead.

    This is exactly the harness worker's shape: the runner spawns it and never
    ``wait()``s it, so ADVANCE must see the finished worker as dead (not a lingering
    ``/proc`` entry at the same start time) to judge it.
    """
    probe = LinuxProcessProbe()
    proc = subprocess.Popen(["true"])
    start = _await_start_time(probe, proc.pid)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if is_zombie(proc.pid):
            break
        time.sleep(0.02)
    assert is_zombie(proc.pid), "child did not become a zombie"
    assert not probe.is_alive(proc.pid, start)
    proc.wait()  # reap it so the test process leaves no zombie behind


def _await_start_time(probe: LinuxProcessProbe, pid: int) -> str:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        start = probe.start_time(pid)
        if start is not None:
            return start
        time.sleep(0.01)
    raise AssertionError("never read the child's start time")
