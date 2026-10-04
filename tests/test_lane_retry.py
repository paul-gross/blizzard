"""The lane retry helper (unit tier) — the backoff formula and the outage latch, pure, no store."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from blizzard.foundation.lane_retry import BACKOFF_CAP, OutageLatch, backoff_delay

pytestmark = pytest.mark.unit

_EVERY = timedelta(seconds=60)
_NOW = datetime(2026, 1, 1)


def test_backoff_doubles_from_the_base_up_to_the_cap() -> None:
    delays = [backoff_delay(n, _EVERY, BACKOFF_CAP) for n in range(1, 8)]
    assert delays == [
        timedelta(seconds=60),
        timedelta(seconds=120),
        timedelta(seconds=240),
        timedelta(seconds=480),
        BACKOFF_CAP,
        BACKOFF_CAP,
        BACKOFF_CAP,
    ]
    assert timedelta(minutes=10) == BACKOFF_CAP


def test_backoff_honours_the_cap_it_is_given() -> None:
    assert backoff_delay(10, timedelta(seconds=60), timedelta(hours=1)) == timedelta(hours=1)
    assert backoff_delay(3, timedelta(seconds=60), timedelta(hours=1)) == timedelta(seconds=240)


def test_backoff_survives_a_very_long_outage() -> None:
    assert backoff_delay(10_000, _EVERY, BACKOFF_CAP) == BACKOFF_CAP


def test_backoff_with_no_failure_is_the_base() -> None:
    assert backoff_delay(0, _EVERY, BACKOFF_CAP) == _EVERY


def test_a_fresh_latch_is_due_and_the_first_failure_opens_an_outage() -> None:
    latch = OutageLatch(_EVERY, lambda: False)
    assert latch.is_due(_NOW)
    assert latch.failed(_NOW) is True
    assert latch.failures == 1
    assert not latch.is_due(_NOW + _EVERY - timedelta(seconds=1))
    assert latch.is_due(_NOW + _EVERY)


def test_further_failures_back_off_without_reopening() -> None:
    latch = OutageLatch(_EVERY, lambda: False)
    latch.failed(_NOW)
    assert latch.failed(_NOW + _EVERY) is False
    assert latch.retry_in == 2 * _EVERY
    assert not latch.is_due(_NOW + _EVERY + 2 * _EVERY - timedelta(seconds=1))


def test_a_success_closes_only_an_open_outage_and_clears_the_backoff() -> None:
    latch = OutageLatch(_EVERY, lambda: False)
    assert latch.succeeded() is False
    latch.failed(_NOW)
    assert latch.succeeded() is True
    assert latch.failures == 0
    assert latch.is_due(_NOW)
    assert latch.succeeded() is False


def test_a_latch_seeded_failing_does_not_announce_again_but_does_announce_recovery() -> None:
    latch = OutageLatch(_EVERY, lambda: True)
    assert latch.failed(_NOW) is False
    assert latch.succeeded() is True


def test_the_reader_is_consulted_once_and_only_on_first_use() -> None:
    reads: list[int] = []

    def reader() -> bool:
        reads.append(1)
        return False

    latch = OutageLatch(_EVERY, reader)
    assert reads == []
    latch.is_due(_NOW)
    latch.failed(_NOW)
    latch.succeeded()
    assert reads == [1]


def test_a_reader_that_raises_leaves_the_latch_unseeded() -> None:
    answers = iter([RuntimeError("store down"), True])

    def reader() -> bool:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    latch = OutageLatch(_EVERY, reader)
    with pytest.raises(RuntimeError):
        latch.is_due(_NOW)
    assert latch.failed(_NOW) is False
