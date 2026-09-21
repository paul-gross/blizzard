"""Process-liveness by (pid, start_time) — the reuse-proof reap signal.

Exercised against real OS processes: the current process is alive at its own start
time, a never-existent pid is dead, and a mismatched start time (the pid-reuse case)
reads as dead even when the pid is live.
"""

from __future__ import annotations

import contextlib
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


@pytest.mark.unit
def test_group_alive_reaps_an_exited_leader_so_it_reads_dead() -> None:
    """An exited-but-unreaped leader is a zombie `killpg`'s signal-0 probe still reaches —
    unlike `is_alive`. Unreaped, `group_alive` would read it alive forever, so a waiter
    polling it for exit (the drain's own wait loop) never observes the transition."""
    probe = LinuxProcessProbe()
    proc = subprocess.Popen(["true"], start_new_session=True)  # `start_new_session` -> pgid == pid
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ProcStat.of(proc.pid).zombie:
            break
        time.sleep(0.02)
    assert ProcStat.of(proc.pid).zombie, "child did not become a zombie"
    assert not probe.group_alive(proc.pid)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=0)  # `group_alive` already reaped it — a no-op, not a hang


@pytest.mark.unit
def test_group_alive_leaves_a_still_running_leader_untouched() -> None:
    """The non-blocking reap must never wait on a live leader — only an already-exited one."""
    probe = LinuxProcessProbe()
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert probe.group_alive(proc.pid)
        assert proc.poll() is None  # still running — `group_alive` did not reap it away
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, 9)
        proc.wait()


@pytest.mark.unit
def test_interrupt_group_signals_a_real_process_group() -> None:
    """``interrupt_group`` reaches a real throwaway process group: a child with no SIGINT
    handler dies on it, exactly the graceful-shutdown drain's own signal (issue #12)."""
    probe = LinuxProcessProbe()
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        probe.interrupt_group(proc.pid)  # `start_new_session` makes pgid == pid
        proc.wait(timeout=5)
        assert proc.returncode == -2  # killed by SIGINT
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, 9)
        proc.wait()


@pytest.mark.unit
def test_interrupt_group_of_an_already_gone_group_is_a_noop() -> None:
    probe = LinuxProcessProbe()
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    probe.interrupt_group(proc.pid)  # no live group left — must not raise


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
