"""``PeriodicPassDriver`` — the off-tick daemon thread the trace sweep and the credential-renewal
pass each run on (unit tier): it passes after its jitter, survives a raising pass, and its
``stop()`` is bounded by a hung one."""

from __future__ import annotations

import threading
import time

import pytest

from blizzard.foundation.logging import get_logger
from blizzard.foundation.periodic_pass_driver import PeriodicPassDriver

pytestmark = pytest.mark.unit

_LOG = get_logger("tests.periodic_pass_driver")


class _Counting:
    def __init__(self, *, raises: bool = False) -> None:
        self.passes = 0
        self.raises = raises

    def run(self) -> None:
        self.passes += 1
        if self.raises:
            raise RuntimeError("bad pass")


def _driver(run_pass: _Counting | None = None, **kwargs: float) -> PeriodicPassDriver:
    return PeriodicPassDriver((run_pass or _Counting()).run, name="test-pass", label="test pass", log=_LOG, **kwargs)


def test_the_driver_passes_after_its_jitter_and_survives_a_raising_pass() -> None:
    counting = _Counting(raises=True)
    driver = _driver(counting, interval_seconds=0.01, jitter_seconds=0)
    driver.start()
    try:
        deadline = time.monotonic() + 5
        while counting.passes < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        driver.stop()
    assert counting.passes >= 3


def test_the_driver_stop_is_bounded_by_a_hung_pass() -> None:
    release = threading.Event()

    def _hung() -> None:
        release.wait(10)

    driver = PeriodicPassDriver(
        _hung,
        name="test-pass",
        label="test pass",
        log=_LOG,
        interval_seconds=60,
        jitter_seconds=0,
        stop_timeout_seconds=0.2,
    )
    driver.start()
    time.sleep(0.05)
    started = time.monotonic()
    driver.stop()
    assert time.monotonic() - started < 2
    release.set()


def test_the_driver_never_passes_before_its_jitter() -> None:
    counting = _Counting()
    driver = _driver(counting, interval_seconds=60, jitter_seconds=60)
    driver.start()
    time.sleep(0.05)
    driver.stop()
    assert counting.passes == 0
