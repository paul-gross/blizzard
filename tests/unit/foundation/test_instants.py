"""The guarded instant parsers: one answer per input, never a raise."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.instants import epoch_millis_instant, epoch_seconds_instant, iso_instant, reset_instant

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


@pytest.mark.parametrize("value", [*_BAD_NUMBERS, "1785607200"])
def test_epoch_seconds_refuses_what_is_no_instant(value: object) -> None:
    assert epoch_seconds_instant(value) is None


@pytest.mark.parametrize("value", [*_BAD_NUMBERS, "1785607200000"])
def test_epoch_millis_refuses_what_is_no_instant(value: object) -> None:
    assert epoch_millis_instant(value) is None


def test_epoch_inputs_read_in_the_unit_the_function_names() -> None:
    assert epoch_seconds_instant(_T.timestamp()) == _T
    assert epoch_seconds_instant(int(_T.timestamp())) == _T
    assert epoch_millis_instant(_T.timestamp() * 1000) == _T
    assert epoch_seconds_instant(0) == datetime(1970, 1, 1, tzinfo=UTC)
    assert epoch_seconds_instant(-1) == datetime(1969, 12, 31, 23, 59, 59, tzinfo=UTC)


@pytest.mark.parametrize(
    "value", ["2026-08-01T18:00:00Z", "2026-08-01T18:00:00+00:00", "2026-08-01T20:00:00+02:00", "2026-08-01T18:00:00"]
)
def test_iso_reads_zoned_and_naive_strings_as_the_same_utc_instant(value: str) -> None:
    assert iso_instant(value) == _T


@pytest.mark.parametrize("value", ["soon", "", "9999-12-31T23:59:59-23:00", 5, True, None])
def test_iso_refuses_what_is_no_instant(value: object) -> None:
    assert iso_instant(value) is None


def test_reset_instant_takes_seconds_or_iso() -> None:
    assert reset_instant(_T.timestamp()) == reset_instant("2026-08-01T18:00:00Z") == _T


@pytest.mark.parametrize("value", [*_BAD_NUMBERS, "soon"])
def test_reset_instant_refuses_what_is_no_instant(value: object) -> None:
    assert reset_instant(value) is None
