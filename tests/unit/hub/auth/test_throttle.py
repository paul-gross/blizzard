"""``IpThrottle``'s configured capacity and refill, and ``[auth.login_throttle]``'s parse (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.config_keys import ConfigView
from blizzard.hub.auth.throttle import IpThrottle, TokenBucket
from blizzard.hub.config import AuthConfig, ConfigError
from blizzard.hub.config_keys import HUB_REGISTRY


def test_throttle_admits_capacity_then_refuses_then_refills_one_token() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    throttle = IpThrottle(clock=clock, capacity=3, refill_per_second=1 / 30)

    assert [throttle.allow("10.0.0.1") for _ in range(3)] == [True, True, True]
    assert throttle.allow("10.0.0.1") is False

    clock.advance(timedelta(seconds=30))
    assert throttle.allow("10.0.0.1") is True
    assert throttle.allow("10.0.0.1") is False


def _auth(table: dict[str, object]) -> AuthConfig:
    root = ConfigView.root(HUB_REGISTRY, {"auth": {"login_throttle": table}}, strict=True)
    return AuthConfig.of(root.table("auth"))


def test_login_throttle_defaults_are_todays_values() -> None:
    throttle = _auth({}).login_throttle
    assert (throttle.enabled, throttle.capacity, throttle.refill_seconds) == (True, 10, 6)


def test_login_throttle_reads_the_table() -> None:
    throttle = _auth({"enabled": False, "capacity": 2, "refill_seconds": 1.5}).login_throttle
    assert (throttle.enabled, throttle.capacity, throttle.refill_seconds) == (False, 2, 1.5)


@pytest.mark.parametrize(
    ("table", "key"),
    [
        ({"enabled": "false"}, "enabled"),
        ({"capacity": 0}, "capacity"),
        ({"capacity": -1}, "capacity"),
        ({"refill_seconds": 0}, "refill_seconds"),
        ({"refill_seconds": -6}, "refill_seconds"),
    ],
)
def test_invalid_login_throttle_is_refused_naming_the_key(table: dict[str, object], key: str) -> None:
    with pytest.raises(ConfigError, match=f"auth.login_throttle.{key}"):
        _auth(table)


def test_bucket_admit_refills_by_elapsed_time_up_to_capacity() -> None:
    bucket = TokenBucket(tokens=0.0, last_refill_seconds=100.0)
    assert bucket.admit(100.0, capacity=2, refill_per_second=0.5) is False
    assert bucket.admit(102.0, capacity=2, refill_per_second=0.5) is True
    assert bucket.tokens == 0.0
    assert bucket.admit(1000.0, capacity=2, refill_per_second=0.5) is True
    assert bucket.tokens == 1.0


def test_bucket_admit_ignores_a_clock_that_ran_backwards() -> None:
    bucket = TokenBucket(tokens=1.0, last_refill_seconds=100.0)
    assert bucket.admit(50.0, capacity=2, refill_per_second=1.0) is True
    assert bucket.tokens == 0.0
    assert bucket.last_refill_seconds == 50.0


def test_live_throttle_replaces_buckets_on_edits_and_disable() -> None:
    from blizzard.hub.auth.throttle import LiveIpThrottle

    throttle = LiveIpThrottle(clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)))

    def allow(enabled: bool = True, capacity: int = 1, refill: float = 6) -> bool:
        with throttle.capture(lambda: (None, (enabled, capacity, refill))) as (_, generation):
            return throttle.allow("ip", generation)

    assert allow()
    assert not allow()
    assert allow(refill=7)
    assert not allow(refill=7)
    assert allow(enabled=False, refill=7)
    assert allow(refill=7)
    assert not allow(refill=7)
    assert allow(capacity=2, refill=7)
    assert allow(capacity=2, refill=7)
    assert not allow(capacity=2, refill=7)


@pytest.mark.parametrize("seen_first", [False, True])
def test_delayed_captured_request_keeps_its_generation_until_release(seen_first: bool) -> None:
    from blizzard.hub.auth.throttle import LiveIpThrottle

    throttle = LiveIpThrottle(clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)))
    a = (True, 1, 60.0)
    b = (True, 2, 60.0)
    with throttle.capture(lambda: (1, a)) as (_, old):
        if seen_first:
            assert throttle.allow("ip", old)
        with throttle.capture(lambda: (2, b)) as (_, middle):
            assert throttle.allow("ip", middle)
            with throttle.capture(lambda: (3, a)) as (_, current):
                assert throttle.allow("ip", current)
                assert throttle.allow("ip", old) is not seen_first
                assert not throttle.allow("ip", old)
                assert not throttle.allow("ip", current)
                assert len(throttle._retired) == 2  # pyright: ignore[reportPrivateUsage]  # generation retention
            assert len(throttle._retired) == 2  # pyright: ignore[reportPrivateUsage]  # generation retention
        assert len(throttle._retired) == 1  # pyright: ignore[reportPrivateUsage]  # generation retention
    assert not throttle._retired  # pyright: ignore[reportPrivateUsage]  # generation cleanup


def test_retired_generations_are_cleaned_up_on_exception() -> None:
    from blizzard.hub.auth.throttle import LiveIpThrottle

    throttle = LiveIpThrottle(clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)))
    with (
        pytest.raises(ValueError),
        throttle.capture(lambda: (None, (True, 1, 60.0))),
        throttle.capture(lambda: (None, (True, 2, 60.0))),
    ):
        raise ValueError("request failed")
    assert not throttle._retired  # pyright: ignore[reportPrivateUsage]  # generation cleanup


def test_unreferenced_generations_do_not_accumulate() -> None:
    from blizzard.hub.auth.throttle import LiveIpThrottle

    throttle = LiveIpThrottle(clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)))
    for capacity in range(1, 100):
        with throttle.capture(lambda capacity=capacity: (None, (True, capacity, 60.0))):
            pass
    assert not throttle._retired  # pyright: ignore[reportPrivateUsage]  # generation cleanup
