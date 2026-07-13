"""Process-liveness by (pid, start_time) — the reuse-proof reap signal.

Exercised against real OS processes: the current process is alive at its own start
time, a never-existent pid is dead, and a mismatched start time (the pid-reuse case)
reads as dead even when the pid is live.
"""

from __future__ import annotations

import os

import pytest

from blizzard.foundation.process import read_process_start_time
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
