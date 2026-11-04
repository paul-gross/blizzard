"""Unit coverage for the controlled clock and its fence (``bzh:injected-clock``)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta, timezone

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import (
    ENV_CLOCK,
    ENV_CLOCK_FENCE,
    ClockBackwards,
    ClockMove,
    ControlledClock,
    SystemClock,
    resolve_clock,
)

pytestmark = pytest.mark.unit

ANCHOR = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
NAIVE = datetime(2030, 1, 1, 12, 0)


def test_reads_its_anchor_until_moved() -> None:
    clock = ControlledClock(ANCHOR)
    assert clock.now() == ANCHOR
    assert clock.now() == ANCHOR


def test_set_to_a_later_instant_moves_and_returns_both_readings() -> None:
    clock = ControlledClock(ANCHOR)
    later = ANCHOR + timedelta(hours=3)
    assert clock.set(later) == ClockMove(previous=ANCHOR, now=later)
    assert clock.now() == later


def test_advance_by_a_positive_duration_moves_and_returns_both_readings() -> None:
    clock = ControlledClock(ANCHOR)
    assert clock.advance(timedelta(minutes=5)) == ClockMove(previous=ANCHOR, now=ANCHOR + timedelta(minutes=5))
    assert clock.now() == ANCHOR + timedelta(minutes=5)


def test_set_to_the_current_reading_and_advance_by_zero_change_nothing() -> None:
    clock = ControlledClock(ANCHOR)
    assert clock.set(ANCHOR) == ClockMove(previous=ANCHOR, now=ANCHOR)
    assert clock.advance(timedelta(0)) == ClockMove(previous=ANCHOR, now=ANCHOR)
    assert clock.now() == ANCHOR


def test_set_to_an_earlier_instant_is_refused_and_leaves_the_reading() -> None:
    clock = ControlledClock(ANCHOR)
    earlier = ANCHOR - timedelta(seconds=1)
    with pytest.raises(ClockBackwards) as refused:
        clock.set(earlier)
    assert refused.value.current == ANCHOR
    assert refused.value.asked == earlier
    assert not isinstance(refused.value, ValueError)
    assert clock.now() == ANCHOR


def test_malformed_input_is_refused_with_value_error() -> None:
    clock = ControlledClock(ANCHOR)
    with pytest.raises(ValueError):
        clock.advance(timedelta(seconds=-1))
    with pytest.raises(ValueError):
        clock.set(NAIVE)
    with pytest.raises(ValueError):
        clock.advance(timedelta.max)
    with pytest.raises(ValueError):
        ControlledClock(NAIVE)
    assert clock.now() == ANCHOR


def test_concurrent_readers_see_only_set_readings_and_never_go_back() -> None:
    clock = ControlledClock(ANCHOR)
    steps = 500
    written = {ANCHOR + timedelta(seconds=i) for i in range(steps + 1)}
    done = threading.Event()
    failures: list[str] = []

    def reader() -> None:
        last = ANCHOR
        while not done.is_set():
            reading = clock.now()
            if reading not in written:
                failures.append(f"unwritten reading {reading}")
            if reading < last:
                failures.append(f"went back from {last} to {reading}")
            last = reading

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for thread in readers:
        thread.start()
    try:
        for i in range(1, steps + 1):
            if i % 2:
                clock.set(ANCHOR + timedelta(seconds=i))
            else:
                clock.advance(timedelta(seconds=i) - (clock.now() - ANCHOR))
    finally:
        done.set()
        for thread in readers:
            thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in readers)
    assert failures == []


def test_asked_and_fenced_resolves_a_controlled_clock_at_machine_time() -> None:
    before = datetime.now(UTC)
    with capture_logs() as logs:
        clock = resolve_clock({ENV_CLOCK: "controlled", ENV_CLOCK_FENCE: "1"})
    assert isinstance(clock, ControlledClock)
    assert before <= clock.now() <= datetime.now(UTC)
    assert logs == []


@pytest.mark.parametrize(
    "environ", [{}, {ENV_CLOCK: "real"}, {ENV_CLOCK: "real", ENV_CLOCK_FENCE: "1"}, {ENV_CLOCK_FENCE: "1"}]
)
def test_unasked_resolves_the_system_clock_silently(environ: dict[str, str]) -> None:
    with capture_logs() as logs:
        clock = resolve_clock(environ)
    assert isinstance(clock, SystemClock)
    assert logs == []


@pytest.mark.parametrize("environ", [{ENV_CLOCK: "controlled"}, {ENV_CLOCK: "controlled", ENV_CLOCK_FENCE: "true"}])
def test_asked_without_the_fence_warns_once_and_resolves_the_system_clock(environ: dict[str, str]) -> None:
    with capture_logs() as logs:
        clock = resolve_clock(environ)
    assert isinstance(clock, SystemClock)
    [entry] = logs
    assert entry["log_level"] == "warning"
    assert entry["clock_variable"] == ENV_CLOCK
    assert entry["fence_variable"] == ENV_CLOCK_FENCE


def test_an_aware_non_utc_anchor_is_accepted() -> None:
    zoned = datetime(2030, 1, 1, tzinfo=timezone(timedelta(hours=2)))
    assert ControlledClock(zoned).now() == zoned
