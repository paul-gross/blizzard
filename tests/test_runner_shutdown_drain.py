"""The graceful-shutdown worker drain (issue #12) — ``ShutdownDrain`` on its own, driven
entirely off a virtual clock, a scripted probe, and a recording sleep. No real process:
``tests/test_runner_process.py`` proves ``interrupt_group`` against one instead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.loop.shutdown_drain import SHUTDOWN_DRAIN_DEADLINE, ShutdownDrain
from tests.runner_fakes import FakeProbe

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _lease_record(**overrides: object) -> LeaseRecord:
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "runner_id": "r1",
        "retries_max": 2,
        "created_at": _NOW,
        "pid": 100,
        "process_start_time": "start-100",
        "session_id": "sess-a",
        "pgid": 100,
    }
    fields.update(overrides)
    return LeaseRecord(**fields)  # type: ignore[arg-type]


@pytest.mark.unit
def test_drain_of_no_marked_leases_signals_and_sleeps_nothing() -> None:
    probe = FakeProbe()
    sleeps: list[float] = []

    ShutdownDrain(process=probe, clock=FixedClock(_NOW), sleep=sleeps.append).run([])

    assert probe.interrupted_groups == []
    assert sleeps == []


@pytest.mark.unit
def test_drain_interrupts_the_marked_groups_pgid_and_stops_once_it_exits() -> None:
    """A worker that exits on its own SIGINT is never SIGKILLed — the poll that observes
    it gone is the drain's last one."""
    probe = FakeProbe(alive={(100, "start-100")}, groups_alive={100})
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        probe.groups_alive.discard(100)  # the worker exits on the SIGINT this drain just sent

    ShutdownDrain(process=probe, clock=FixedClock(_NOW), sleep=sleep).run([_lease_record()])

    assert probe.interrupted_groups == [100]
    assert probe.killed_groups == []
    assert sleeps == [0.5]


@pytest.mark.unit
def test_drain_sigkills_a_survivor_once_the_shared_deadline_passes() -> None:
    """A worker that ignores SIGINT is SIGKILLed once the deadline elapses, and the drain
    returns rather than waiting further (issue #12 acceptance criterion 4)."""
    probe = FakeProbe(alive={(100, "start-100")}, groups_alive={100})
    clock = FixedClock(_NOW)

    def sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))

    ShutdownDrain(process=probe, clock=clock, sleep=sleep, deadline_seconds=1.0).run([_lease_record()])

    assert probe.interrupted_groups == [100]
    assert probe.killed_groups == [100]


@pytest.mark.unit
def test_drain_skips_a_lease_with_no_recorded_pgid() -> None:
    """An unknowing caller left `pgid` unrecorded (D3) — nothing durable to signal, so the
    drain leaves it for the unit's own final cgroup teardown rather than guessing a group."""
    probe = FakeProbe(alive={(100, "start-100")})

    def sleep(seconds: float) -> None:
        raise AssertionError("nothing to wait for — must not sleep")

    ShutdownDrain(process=probe, clock=FixedClock(_NOW), sleep=sleep).run([_lease_record(pgid=None)])

    assert probe.interrupted_groups == []
    assert probe.killed_groups == []


@pytest.mark.unit
def test_drain_never_signals_a_lease_whose_recorded_leader_is_already_dead() -> None:
    """The same leader-identity guard `kill_owned_process` uses: a dead leader means a
    recycled pgid could belong to an unrelated process by now, so it is never signalled —
    even though the group itself still reads alive."""
    probe = FakeProbe(alive=set(), groups_alive={100})

    def sleep(seconds: float) -> None:
        raise AssertionError("nothing was signalled — must not sleep")

    ShutdownDrain(process=probe, clock=FixedClock(_NOW), sleep=sleep).run([_lease_record()])

    assert probe.interrupted_groups == []
    assert probe.killed_groups == []


@pytest.mark.unit
def test_drain_shares_one_deadline_across_every_marked_worker() -> None:
    """The 60s (here 1s) budget is spent once, not once per worker: a survivor is
    SIGKILLed at the same deadline a faster-exiting sibling never reaches."""
    probe = FakeProbe(alive={(100, "start-100"), (200, "start-200")}, groups_alive={100, 200})
    clock = FixedClock(_NOW)
    calls = 0

    def sleep(seconds: float) -> None:
        nonlocal calls
        calls += 1
        clock.advance(timedelta(seconds=seconds))
        if calls == 1:
            probe.groups_alive.discard(100)  # the first worker exits after the first poll

    leases = [
        _lease_record(),
        _lease_record(lease_id="lease_2", pid=200, process_start_time="start-200", session_id="sess-b", pgid=200),
    ]
    ShutdownDrain(process=probe, clock=clock, sleep=sleep, deadline_seconds=1.0).run(leases)

    assert set(probe.interrupted_groups) == {100, 200}
    assert probe.killed_groups == [200]  # only the survivor


@pytest.mark.unit
def test_shutdown_drain_deadline_default_is_sixty_seconds() -> None:
    assert SHUTDOWN_DRAIN_DEADLINE == 60.0
