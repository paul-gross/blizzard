"""``HarnessHealthCache`` (blizzard#438) — the composition-root-owned cache that recomputes
a harness's health at its own bounded refresh window, on an observed version change, or
when a new selftest result lands, and never merely because a peek asked."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.selftest_result import SelfTestResultRecord
from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.loop.capability_snapshot import HarnessHealthCache

pytestmark = pytest.mark.unit

_HARNESS_ID = "opencode"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class _FakeProbe:
    binary: bool = True
    authenticated: bool = True
    supported: str | None = None
    degradations: tuple[DeclaredDegradation, ...] = ()
    calls: int = field(default=0, compare=False)

    def binary_present(self) -> bool:
        self.calls += 1
        return self.binary

    def probe_authentication(self) -> bool:
        return self.authenticated

    def supported_version(self) -> str | None:
        return self.supported

    def declared_degradations(self) -> tuple[DeclaredDegradation, ...]:
        return self.degradations


@dataclass
class _FakeAdapter:
    unresolvable: tuple[str, ...] = ()

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None:
        return None if preferences and preferences[0] in self.unresolvable else "native"


@dataclass
class _FakeSelftestResults:
    record: SelfTestResultRecord | None = None

    def latest_selftest_result(self, harness_id: str) -> SelfTestResultRecord | None:
        del harness_id
        return self.record


def _cache(probe: _FakeProbe, results: _FakeSelftestResults, *, clock: FixedClock) -> HarnessHealthCache:
    return HarnessHealthCache(clock=clock, probes={_HARNESS_ID: probe}, selftest_results=results)


def test_unknown_harness_reports_no_result() -> None:
    cache = HarnessHealthCache(clock=FixedClock(_NOW), probes={}, selftest_results=_FakeSelftestResults())
    assert cache.refresh("unknown", adapter=_FakeAdapter(), observed_version=None) is None
    assert cache.get("unknown") is None


def test_observed_version_is_none_before_any_refresh() -> None:
    cache = HarnessHealthCache(clock=FixedClock(_NOW), probes={}, selftest_results=_FakeSelftestResults())
    assert cache.observed_version(_HARNESS_ID) is None


def test_observed_version_reads_back_what_refresh_was_given() -> None:
    clock = FixedClock(_NOW)
    cache = _cache(_FakeProbe(), _FakeSelftestResults(), clock=clock)
    cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.0")
    assert cache.observed_version(_HARNESS_ID) == "1.0"


def test_first_refresh_computes_and_caches() -> None:
    clock = FixedClock(_NOW)
    probe = _FakeProbe()
    cache = _cache(probe, _FakeSelftestResults(), clock=clock)
    result = cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.0")
    assert result is not None
    assert result.available is True
    assert probe.calls == 1
    assert cache.get(_HARNESS_ID) is result


def test_repeat_refresh_within_window_reuses_the_cached_result() -> None:
    clock = FixedClock(_NOW)
    probe = _FakeProbe()
    cache = _cache(probe, _FakeSelftestResults(), clock=clock)
    cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.0")
    clock.advance(timedelta(seconds=1))
    cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.0")
    assert probe.calls == 1  # no second probe — nothing changed and the window has not elapsed


def test_refresh_after_the_window_elapses_recomputes() -> None:
    clock = FixedClock(_NOW)
    probe = _FakeProbe()
    cache = HarnessHealthCache(
        clock=clock, probes={_HARNESS_ID: probe}, selftest_results=_FakeSelftestResults(), refresh_seconds=60.0
    )
    cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.0")
    clock.advance(timedelta(seconds=61))
    cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.0")
    assert probe.calls == 2


def test_an_observed_version_change_forces_an_immediate_recompute() -> None:
    clock = FixedClock(_NOW)
    probe = _FakeProbe()
    cache = _cache(probe, _FakeSelftestResults(), clock=clock)
    cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.0")
    cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.1")
    assert probe.calls == 2


def test_a_new_selftest_result_forces_an_immediate_recompute() -> None:
    clock = FixedClock(_NOW)
    probe = _FakeProbe()
    results = _FakeSelftestResults()
    cache = _cache(probe, results, clock=clock)
    cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.0")
    results.record = SelfTestResultRecord(harness_id=_HARNESS_ID, status="failed", error="boom", recorded_at=_NOW)
    result = cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.0")
    assert probe.calls == 2
    assert result is not None
    assert result.available is False


def test_unmapped_configured_tier_is_reported() -> None:
    clock = FixedClock(_NOW)
    probe = _FakeProbe()
    cache = HarnessHealthCache(
        clock=clock,
        probes={_HARNESS_ID: probe},
        selftest_results=_FakeSelftestResults(),
        configured_tiers={_HARNESS_ID: (("blizzard:custom", ""),)},
    )
    result = cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(unresolvable=("blizzard:custom",)), observed_version="1.0")
    assert result is not None
    assert result.available is False
