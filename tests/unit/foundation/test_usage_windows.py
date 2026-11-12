"""``admit_usage_window``'s ``resets_at`` rule: refused by name, never raised."""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from blizzard.foundation.usage_windows import AdmittedUsageWindow, admit_usage_window


def _entry(resets_at: object) -> dict[str, object]:
    return {"window": "5h", "utilization_pct": 10.0, "resets_at": resets_at, "window_seconds": 18_000}


@pytest.mark.parametrize(
    "resets_at",
    [10**400, -(10**400), 1e18, "1e30", -1e30, 1e30, True, float("nan"), "nan", "inf", "soon", None],
)
def test_a_reset_that_is_no_instant_is_refused_by_name(resets_at: object) -> None:
    assert admit_usage_window(_entry(resets_at)) == "resets_at"


@pytest.mark.parametrize(
    "resets_at", [1_785_607_200, "1785607200", 1_785_607_200_000, "1785607200000", "2026-08-01T18:00:00Z"]
)
def test_seconds_milliseconds_numeric_strings_and_iso_keep_being_admitted(resets_at: object) -> None:
    admitted = admit_usage_window(_entry(resets_at))
    assert isinstance(admitted, AdmittedUsageWindow)
    assert admitted.resets_at == datetime(2026, 8, 1, 18, 0, tzinfo=UTC)


def test_a_non_dict_mapping_is_admitted() -> None:
    entry = _entry("2026-08-01T18:00:00Z")
    assert admit_usage_window(MappingProxyType(entry)) == admit_usage_window(entry)
    assert isinstance(admit_usage_window(MappingProxyType(entry)), AdmittedUsageWindow)


@pytest.mark.parametrize(
    "pct", [10**400, -(10**400), float("nan"), float("inf"), float("-inf"), -1, 100.5, True], ids=str
)
def test_a_utilization_that_is_no_percentage_is_refused_by_name(pct: object) -> None:
    entry = {**_entry(1_785_607_200), "utilization_pct": pct}
    assert admit_usage_window(entry) == "utilization_pct"


def test_an_offset_reset_is_admitted_as_its_utc_instant() -> None:
    admitted = admit_usage_window(_entry("2026-08-01T20:00:00+02:00"))
    assert isinstance(admitted, AdmittedUsageWindow)
    assert admitted.resets_at == datetime(2026, 8, 1, 18, 0, tzinfo=UTC)
    assert admitted.stored["resets_at"] == "2026-08-01T18:00:00+00:00"
