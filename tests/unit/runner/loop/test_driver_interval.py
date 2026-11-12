"""Both timed drivers — ``PeriodicDriver`` and ``PeriodicPassDriver`` — wait their whole interval from the end of one
pass to the start of the next, read the interval before each wait, and end a wait at once when stopped (unit tier)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, cast

import pytest

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.periodic_pass_driver import PeriodicPassDriver
from blizzard.runner.loop_wiring import PeriodicDriver

_LOG = get_logger("tests.driver_interval")


class _TimedPasses:
    """A pass that runs for ``duration`` seconds, recording when each started and ended."""

    def __init__(self, *, duration: float = 0.0) -> None:
        self.duration = duration
        self.spans: list[tuple[float, float]] = []
        self.ran = threading.Event()

    def run(self) -> None:
        started = time.monotonic()
        time.sleep(self.duration)
        self.spans.append((started, time.monotonic()))
        self.ran.set()

    def wait_for(self, count: int) -> None:
        deadline = time.monotonic() + 10
        while len(self.spans) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(self.spans) >= count


class _Stepper:
    """The slice of ``RunnerStepper`` ``PeriodicDriver`` drives: its wiring and the timed tick."""

    def __init__(self, passes: _TimedPasses) -> None:
        self.wiring = cast(Any, type("_Wiring", (), {"config": type("_Config", (), {"name": "r"})()})())
        self.timed_tick = passes.run


def _runner_driver(passes: _TimedPasses, interval: Callable[[], float]) -> PeriodicDriver:
    return PeriodicDriver(
        cast(Any, None), interval=interval, process_graph=cast(Any, None), stepper=cast(Any, _Stepper(passes))
    )


def _pass_driver(passes: _TimedPasses, interval: Callable[[], float]) -> PeriodicPassDriver:
    return PeriodicPassDriver(
        passes.run, name="test-pass", label="test pass", log=_LOG, interval=interval, jitter_seconds=0
    )


_DRIVERS = pytest.mark.parametrize(
    "build", [_runner_driver, _pass_driver], ids=["PeriodicDriver", "PeriodicPassDriver"]
)


@_DRIVERS
def test_a_pass_longer_than_the_interval_is_followed_by_the_whole_interval(
    build: Callable[[_TimedPasses, Callable[[], float]], PeriodicDriver | PeriodicPassDriver],
) -> None:
    passes = _TimedPasses(duration=0.3)
    driver = build(passes, lambda: 0.2)
    driver.start()
    try:
        passes.wait_for(2)
    finally:
        driver.stop()
    (_, first_end), (second_start, _) = passes.spans[:2]
    assert second_start - first_end >= 0.2


@_DRIVERS
def test_the_interval_is_read_before_each_wait(
    build: Callable[[_TimedPasses, Callable[[], float]], PeriodicDriver | PeriodicPassDriver],
) -> None:
    passes = _TimedPasses()
    driver = build(passes, lambda: 0.3 if len(passes.spans) >= 2 else 0.0)
    driver.start()
    try:
        passes.wait_for(3)
    finally:
        driver.stop()
    (_, first_end), (second_start, second_end), (third_start, _) = passes.spans[:3]
    assert third_start - second_end >= 0.3
    assert second_start - first_end < 0.3


@_DRIVERS
def test_a_stop_ends_the_wait_at_once(
    build: Callable[[_TimedPasses, Callable[[], float]], PeriodicDriver | PeriodicPassDriver],
) -> None:
    passes = _TimedPasses()
    driver = build(passes, lambda: 60.0)
    driver.start()
    assert passes.ran.wait(5)
    started = time.monotonic()
    driver.stop()
    assert time.monotonic() - started < 2
    assert len(passes.spans) == 1
