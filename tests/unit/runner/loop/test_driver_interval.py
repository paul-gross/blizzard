"""Both timed drivers — ``PeriodicDriver`` and ``PeriodicPassDriver`` — wait their live interval from the end of one
pass to the start of the next. A wake re-reads it: a driver runs at once when its new due time has passed, and waits out
the rest when it has not. One driver's wake leaves every other driver woken, and a stop ends a wait at once. Driven
through an injected clock and wait, never a sleep (unit tier)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, cast

import pytest

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.periodic_pass_driver import PeriodicPassDriver
from blizzard.runner.loop.wake import IntervalWait, WakeSignal, WakeSignals
from blizzard.runner.loop_wiring import PeriodicDriver

_LOG = get_logger("tests.driver_interval")


class _ManualTime:
    """A monotonic clock only the test moves, and a wait that returns once its signal is set or the clock has
    passed its deadline."""

    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []
        self._cond = threading.Condition()
        self._waiting = 0

    def monotonic(self) -> float:
        with self._cond:
            return self.now

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        with self._cond:
            self.now += seconds
            self._cond.notify_all()

    def wait(self, event: threading.Event, timeout: float) -> object:
        with self._cond:
            self.waits.append(timeout)
            deadline = self.now + timeout
            self._waiting += 1
            self._cond.notify_all()
            try:
                # A set event does not notify the condition, so it is re-checked on a short poll.
                while not event.is_set() and self.now < deadline:
                    self._cond.wait(0.01)
            finally:
                self._waiting -= 1
        return event.is_set()

    def until_waiting(self, count: int) -> None:
        """Block until a driver has entered its ``count``-th wait."""
        with self._cond:
            assert self._cond.wait_for(lambda: self._waiting and len(self.waits) >= count, timeout=10)


class _Passes:
    """A pass recording the injected clock's reading at each start."""

    def __init__(self, time: _ManualTime) -> None:
        self._time = time
        self.starts: list[float] = []
        self._cond = threading.Condition()

    def run(self) -> None:
        with self._cond:
            self.starts.append(self._time.monotonic())
            self._cond.notify_all()

    def wait_for(self, count: int) -> None:
        with self._cond:
            assert self._cond.wait_for(lambda: len(self.starts) >= count, timeout=10)


class _Stepper:
    """The slice of ``RunnerStepper`` ``PeriodicDriver`` drives: its wiring and the timed tick."""

    def __init__(self, passes: _Passes) -> None:
        self.wiring = cast(Any, type("_Wiring", (), {"config": type("_Config", (), {"name": "r"})()})())
        self.timed_tick = passes.run


Build = Callable[[_Passes, Callable[[], float], WakeSignal, _ManualTime], PeriodicDriver | PeriodicPassDriver]


def _runner_driver(
    passes: _Passes, interval: Callable[[], float], signal: WakeSignal, time: _ManualTime
) -> PeriodicDriver:
    return PeriodicDriver(
        cast(Any, None),
        interval=interval,
        process_graph=cast(Any, None),
        stepper=cast(Any, _Stepper(passes)),
        signal=signal,
        clock=time,
        wait=time.wait,
    )


def _pass_driver(
    passes: _Passes, interval: Callable[[], float], signal: WakeSignal, time: _ManualTime
) -> PeriodicPassDriver:
    return PeriodicPassDriver(
        passes.run,
        name="test-pass",
        label="test pass",
        log=_LOG,
        interval=interval,
        jitter_seconds=0,
        signal=signal,
        clock=time,
        wait=time.wait,
    )


_DRIVERS = pytest.mark.parametrize(
    "build", [_runner_driver, _pass_driver], ids=["PeriodicDriver", "PeriodicPassDriver"]
)


def test_an_unwoken_wait_is_the_whole_interval_from_the_end_of_the_pass() -> None:
    time = _ManualTime()
    time.now = 100.0

    def wait(event: threading.Event, timeout: float) -> object:
        time.waits.append(timeout)
        time.now += timeout
        return False

    waits = IntervalWait(lambda: 30.0, signal=WakeSignal(), clock=time, log=_LOG, label="test", wait=wait)
    assert waits.until_due(100.0)
    assert (time.waits, time.now) == ([30.0], 130.0)


def test_a_woken_wait_runs_at_once_when_the_new_due_time_has_passed() -> None:
    time = _ManualTime()
    interval = [3600.0]
    signal = WakeSignal()

    def wait(event: threading.Event, timeout: float) -> object:
        time.waits.append(timeout)
        time.now += 5
        interval[0] = 1.0
        signal.set()
        return True

    waits = IntervalWait(lambda: interval[0], signal=signal, clock=time, log=_LOG, label="test", wait=wait)
    assert waits.until_due(0.0)
    assert (time.waits, time.now) == ([3600.0], 5.0)
    assert not signal.event.is_set()


def test_a_woken_wait_waits_out_the_rest_when_the_new_due_time_has_not_passed() -> None:
    time = _ManualTime()
    interval = [3600.0]
    signal = WakeSignal()

    def wait(event: threading.Event, timeout: float) -> object:
        time.waits.append(timeout)
        if len(time.waits) == 1:
            time.now += 5
            interval[0] = 20.0
            signal.set()
        else:
            time.now += timeout
        return event.is_set()

    waits = IntervalWait(lambda: interval[0], signal=signal, clock=time, log=_LOG, label="test", wait=wait)
    assert waits.until_due(0.0)
    assert (time.waits, time.now) == ([3600.0, 15.0], 20.0)


def test_one_drivers_wake_leaves_every_other_driver_woken() -> None:
    time = _ManualTime()
    wakes = WakeSignals()
    first, second = wakes.signal(), wakes.signal()
    set_on_entry: list[bool] = []

    def wait(event: threading.Event, timeout: float) -> object:
        set_on_entry.append(event.is_set())
        if not set_on_entry[0] and len(set_on_entry) == 1:
            wakes.wake()
        return True

    def shortened_after_a_wake(reads: list[int]) -> Callable[[], float]:
        def read() -> float:
            reads.append(1)
            return 3600.0 if len(reads) == 1 else 0.0

        return read

    assert IntervalWait(shortened_after_a_wake([]), signal=first, clock=time, log=_LOG, label="a", wait=wait).until_due(
        0.0
    )
    # The first driver consumed its own wake; the second's is still pending, so its wait returns at once.
    assert not first.event.is_set()
    assert second.event.is_set()
    assert IntervalWait(
        shortened_after_a_wake([]), signal=second, clock=time, log=_LOG, label="b", wait=wait
    ).until_due(0.0)
    assert set_on_entry == [False, True]


def test_a_stop_ends_a_wait_and_no_pass_follows() -> None:
    time = _ManualTime()
    signal = WakeSignal()
    waits: list[IntervalWait] = []

    def wait(event: threading.Event, timeout: float) -> object:
        waits[0].stop()
        return event.is_set()

    waits.append(IntervalWait(lambda: 3600.0, signal=signal, clock=time, log=_LOG, label="test", wait=wait))
    assert waits[0].until_due(0.0) is False


def test_a_failed_interval_read_keeps_the_last_interval() -> None:
    time = _ManualTime()
    reads = iter([10.0])

    def interval() -> float:
        value = next(reads, None)
        if value is None:
            raise RuntimeError("store down")
        return value

    def wait(event: threading.Event, timeout: float) -> object:
        time.waits.append(timeout)
        time.now += timeout
        return False

    waits = IntervalWait(interval, signal=WakeSignal(), clock=time, log=_LOG, label="test", wait=wait)
    assert waits.until_due(0.0)
    assert waits.until_due(time.now)
    assert time.waits == [10.0, 10.0]


@_DRIVERS
def test_an_unedited_driver_waits_the_full_interval_from_the_end_of_a_pass(build: Build) -> None:
    time = _ManualTime()
    passes = _Passes(time)
    driver = build(passes, lambda: 10.0, WakeSignal(), time)
    driver.start()
    try:
        time.until_waiting(1)
        time.advance(10)
        passes.wait_for(2)
    finally:
        driver.stop()
    assert passes.starts[:2] == [0.0, 10.0]
    assert time.waits[0] == 10.0


@_DRIVERS
def test_a_woken_driver_runs_at_once_on_a_shortened_interval(build: Build) -> None:
    time = _ManualTime()
    passes = _Passes(time)
    interval = [3600.0]
    wakes = WakeSignals()
    driver = build(passes, lambda: interval[0], wakes.signal(), time)
    driver.start()
    try:
        time.until_waiting(1)
        time.advance(5)
        interval[0] = 1.0
        wakes.wake()
        passes.wait_for(2)
    finally:
        driver.stop()
    assert passes.starts[:2] == [0.0, 5.0]


@_DRIVERS
def test_a_stop_ends_the_wait_at_once(build: Build) -> None:
    time = _ManualTime()
    passes = _Passes(time)
    driver = build(passes, lambda: 3600.0, WakeSignal(), time)
    driver.start()
    time.until_waiting(1)
    driver.stop()
    assert passes.starts == [0.0]
