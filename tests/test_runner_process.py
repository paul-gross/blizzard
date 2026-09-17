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

from blizzard.foundation.process import ProcStat
from blizzard.runner.loop.process import LinuxProcessProbe, kill_owned_process
from tests.runner_fakes import FakeProbe

_START = "start-token"


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
    assert ProcStat.of(2**31 - 1).start_time is None
    assert not probe.is_alive(2**31 - 1, "whatever")


@pytest.mark.unit
def test_mismatched_start_time_reads_dead_across_pid_reuse() -> None:
    probe = LinuxProcessProbe()
    pid = os.getpid()  # a live pid, but with the wrong recorded start time
    assert not probe.is_alive(pid, "not-the-real-start-time")


@pytest.mark.unit
def test_exited_but_unreaped_worker_reads_dead() -> None:
    """A fire-and-forget child that exited but is not yet wait()ed is a zombie -> dead —
    exactly the harness worker's shape ADVANCE must see as dead to judge it."""
    probe = LinuxProcessProbe()
    proc = subprocess.Popen(["true"])
    start = _await_start_time(probe, proc.pid)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ProcStat.of(proc.pid).zombie:
            break
        time.sleep(0.02)
    assert ProcStat.of(proc.pid).zombie, "child did not become a zombie"
    assert not probe.is_alive(proc.pid, start)
    proc.wait()  # reap it so the test process leaves no zombie behind


# `kill_owned_process` — the one shared, liveness-checked, pgid-preferring teardown every
# owned-process kill site (attempt, dormant, takeover) routes through (D3).


@pytest.mark.unit
def test_an_owned_kill_prefers_the_recorded_group_over_a_bare_pid() -> None:
    probe = FakeProbe(alive={(4242, _START)}, groups_alive={4242})
    kill_owned_process(probe, pid=4242, process_start_time=_START, pgid=4242)
    assert probe.killed_groups == [4242]
    assert probe.killed == []


@pytest.mark.unit
def test_an_owned_kill_with_no_recorded_group_falls_back_to_the_bare_pid() -> None:
    probe = FakeProbe(alive={(4242, _START)})
    kill_owned_process(probe, pid=4242, process_start_time=_START, pgid=None)
    assert probe.killed == [4242]
    assert probe.killed_groups == []


@pytest.mark.unit
def test_an_owned_kill_still_reaps_the_group_when_only_a_descendant_survives() -> None:
    """The post-crash shape durable group ownership exists for: the recorded leader is
    gone, but a descendant still holds the group, so the `killpg` must still fire."""
    probe = FakeProbe(alive=set(), groups_alive={4242})
    kill_owned_process(probe, pid=4242, process_start_time=_START, pgid=4242)
    assert probe.killed_groups == [4242]


@pytest.mark.unit
def test_an_owned_kill_signals_nothing_when_leader_and_group_are_both_gone() -> None:
    """The pid/pgid-reuse guard: neither the recorded leader nor its group is ours any
    more, so an unrelated process the OS has since given this pid is never signalled."""
    probe = FakeProbe(alive=set(), groups_alive=set())
    kill_owned_process(probe, pid=4242, process_start_time=_START, pgid=4242)
    assert probe.killed == []
    assert probe.killed_groups == []


@pytest.mark.unit
def test_an_owned_kill_with_no_recorded_pid_or_start_time_is_a_no_op() -> None:
    probe = FakeProbe(alive={(4242, _START)}, groups_alive={4242})
    kill_owned_process(probe, pid=None, process_start_time=_START, pgid=4242)
    kill_owned_process(probe, pid=4242, process_start_time=None, pgid=4242)
    assert probe.killed == []
    assert probe.killed_groups == []


def _await_start_time(probe: LinuxProcessProbe, pid: int) -> str:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        start = probe.start_time(pid)
        if start is not None:
            return start
        time.sleep(0.01)
    raise AssertionError("never read the child's start time")
