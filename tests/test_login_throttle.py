"""``IpThrottle``'s configured capacity and refill, and ``[auth.login_throttle]``'s parse (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.config_keys import ConfigView
from blizzard.hub.auth.throttle import IpThrottle
from blizzard.hub.config import AuthConfig, ConfigError
from blizzard.hub.config_keys import HUB_REGISTRY

pytestmark = pytest.mark.unit


def test_throttle_admits_capacity_then_refuses_then_refills_one_token() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    throttle = IpThrottle(clock=clock, capacity=3, refill_per_second=1 / 30)

    assert [throttle.allow("10.0.0.1") for _ in range(3)] == [True, True, True]
    assert throttle.allow("10.0.0.1") is False

    clock.advance(timedelta(seconds=30))
    assert throttle.allow("10.0.0.1") is True
    assert throttle.allow("10.0.0.1") is False


def _auth(table: dict) -> AuthConfig:
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
def test_invalid_login_throttle_is_refused_naming_the_key(table: dict, key: str) -> None:
    with pytest.raises(ConfigError, match=f"auth.login_throttle.{key}"):
        _auth(table)
