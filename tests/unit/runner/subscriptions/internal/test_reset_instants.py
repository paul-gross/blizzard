"""The sampler bindings' shared reset-instant read: one answer per input, never a raise."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.subscriptions.internal.reset_instants import reset_instant

_T = datetime(2026, 8, 1, 18, 0, 0, tzinfo=UTC)
_BAD_NUMBERS: list[object] = [
    True,
    False,
    float("nan"),
    float("inf"),
    float("-inf"),
    1e18,
    1e30,
    -1e30,
    10**400,
    -(10**400),
    None,
    [],
    {},
]


def test_reset_instant_takes_seconds_or_iso() -> None:
    assert reset_instant(_T.timestamp()) == reset_instant("2026-08-01T18:00:00Z") == _T


@pytest.mark.parametrize("value", [*_BAD_NUMBERS, "soon"])
def test_reset_instant_refuses_what_is_no_instant(value: object) -> None:
    assert reset_instant(value) is None
