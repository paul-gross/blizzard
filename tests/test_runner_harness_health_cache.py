"""``HarnessHealthCache`` (blizzard#438) — the composition-root-owned cache that recomputes
a harness's health at its own bounded refresh window, on an observed version change, or
when a new selftest result lands, and never merely because a peek asked."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.selftest_result import SelfTestResultRecord
from blizzard.runner.harness.compatibility import CompatibilityClassification
from blizzard.runner.harness.health import DeclaredDegradation, HarnessHealthCause
from blizzard.runner.harness.internal.opencode_probe import ADMITTED_OPENCODE_RANGE, PINNED_OPENCODE_VERSION
from blizzard.runner.loop import capability_snapshot
from blizzard.runner.loop.capability_snapshot import HarnessHealthCache

pytestmark = pytest.mark.unit

_HARNESS_ID = "opencode"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class _FakeProbe:
    binary: bool = True
    authenticated: bool = True
    supported: SpecifierSet | None = None
    degradations: tuple[DeclaredDegradation, ...] = ()
    calls: int = field(default=0, compare=False)

    def binary_present(self) -> bool:
        self.calls += 1
        return self.binary

    def probe_authentication(self) -> bool:
        return self.authenticated

    def supported_version(self) -> SpecifierSet | None:
        return self.supported

    def supported_version_display(self) -> str | None:
        return str(self.supported) if self.supported is not None else None

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


def test_a_raw_admitted_version_normalizes_and_classifies_against_the_real_corpus() -> None:
    """``refresh`` threads a probe's raw, unnormalized observed version — here prefixed the
    way a real binary's ``--version`` output can be — through the shared normalizer before
    the corpus/membership check, against opencode's own committed corpus (blizzard#438)."""
    clock = FixedClock(_NOW)
    probe = _FakeProbe(supported=ADMITTED_OPENCODE_RANGE)
    cache = _cache(probe, _FakeSelftestResults(), clock=clock)

    result = cache.refresh(
        _HARNESS_ID, adapter=_FakeAdapter(), observed_version=f"opencode version {PINNED_OPENCODE_VERSION}"
    )

    assert result is not None
    # The committed corpus classifies this version `degraded`, not `blocking` — a version
    # cause never fires, and no degradation was declared by this fake probe.
    assert result.available is True
    assert result.cause is None


def test_a_version_above_every_committed_corpus_still_resolves_against_the_real_corpus() -> None:
    """A version above every committed corpus, but inside the admitted range, still resolves
    to a reference corpus rather than reading `unknown_version` — the reference-corpus
    resolution `classify_offline` performs (blizzard#438)."""
    clock = FixedClock(_NOW)
    probe = _FakeProbe(supported=ADMITTED_OPENCODE_RANGE)
    cache = _cache(probe, _FakeSelftestResults(), clock=clock)

    result = cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.18.31")

    assert result is not None
    assert result.available is True
    assert result.cause is None


def test_a_raw_version_outside_the_admitted_range_is_incompatible() -> None:
    """A version genuinely outside the admitted range is `incompatible_version` (D2), reached
    through the real evaluation path — `HarnessHealthCache.refresh` (capability_snapshot.py)
    into `evaluate_harness_health` (health.py) — never a synthetic evidence construction.
    No corpus entry exists for this version at all; the sibling test below pins the harder
    case where one does."""
    clock = FixedClock(_NOW)
    probe = _FakeProbe(supported=ADMITTED_OPENCODE_RANGE)
    cache = _cache(probe, _FakeSelftestResults(), clock=clock)

    result = cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version="1.18.24")

    assert result is not None
    assert result.available is False
    assert result.cause is HarnessHealthCause.INCOMPATIBLE_VERSION


def test_a_non_admitted_version_with_a_real_corpus_entry_still_reads_incompatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Membership is checked before any corpus lookup (D2): a real fixture manifest that would
    classify `supported` on its own still reads `INCOMPATIBLE_VERSION` once non-admitted."""
    stray_version = "1.18.24"
    manifest_dir = tmp_path / "opencode" / stray_version
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({"live_evidence": {"classification": "supported"}}))
    real_classify_offline = capability_snapshot.classify_offline
    monkeypatch.setattr(
        capability_snapshot,
        "classify_offline",
        lambda harness_id, version, admitted_range: real_classify_offline(
            harness_id, version, admitted_range, corpus_root=tmp_path
        ),
    )
    # Prove the fixture alone would classify `supported` once the range actually reaches it,
    # isolating what membership overrides below.
    assert (
        real_classify_offline("opencode", stray_version, SpecifierSet(">=1.0.0,<2.0"), corpus_root=tmp_path)
        is CompatibilityClassification.SUPPORTED
    )

    clock = FixedClock(_NOW)
    probe = _FakeProbe(supported=ADMITTED_OPENCODE_RANGE)
    cache = _cache(probe, _FakeSelftestResults(), clock=clock)

    result = cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version=stray_version)

    assert result is not None
    assert result.available is False
    assert result.cause is HarnessHealthCause.INCOMPATIBLE_VERSION


def test_an_admitted_version_with_no_corpus_manifest_is_unknown_not_incompatible() -> None:
    """A version this fake probe declares admitted, but with no manifest anywhere under the
    real committed corpus root, is `unknown_version` — distinct from a genuinely non-admitted
    version above, reached through the same real evaluation path (blizzard#438, D2)."""
    clock = FixedClock(_NOW)
    unclassifiable_version = "9.9.9"
    probe = _FakeProbe(supported=SpecifierSet(f"=={unclassifiable_version}"))
    cache = _cache(probe, _FakeSelftestResults(), clock=clock)

    result = cache.refresh(_HARNESS_ID, adapter=_FakeAdapter(), observed_version=unclassifiable_version)

    assert result is not None
    assert result.available is False
    assert result.cause is HarnessHealthCause.UNKNOWN_VERSION


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
