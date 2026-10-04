"""The harness rules pinned by value: health's fail-open reading, version admission, reference-corpus
selection, selftest evidence, probe-report refusal, the bundle's published-snapshot requirement, and
the effective autonomy source. Every rule here takes plain values — no probe, store, or clock."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from blizzard.runner.config import effective_autonomy_source
from blizzard.runner.harness.admission import (
    admission_verdict,
    classification_of_manifest,
    select_reference_corpus,
    version_admitted,
)
from blizzard.runner.harness.bundle import HarnessBundleNotPublished, require_published
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.harness.compatibility import (
    CompatibilityClassification,
    CompatibilityContractError,
    CompatibilityReport,
)
from blizzard.runner.harness.health import (
    HarnessHealthCause,
    HarnessHealthEvidence,
    HarnessHealthResult,
    evaluate_harness_health,
    reported_health,
)
from blizzard.runner.harness.selftest_result import LatestSelfTestResult, selftest_failed
from blizzard.runner.harness.wiring import HarnessSections

pytestmark = pytest.mark.unit

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_RANGE = SpecifierSet(">=1.0,<2.0")


def _evidence(*, selftest: bool | None) -> HarnessHealthEvidence:
    return HarnessHealthEvidence(
        harness_id="h",
        binary_present=True,
        version_declared=True,
        version_admitted=True,
        version_classification=CompatibilityClassification.SUPPORTED,
        authenticated=True,
        unmapped_tiers=(),
        selftest_failed=selftest,
    )


def test_unprobed_harness_reads_available() -> None:
    assert reported_health("h", None) == HarnessHealthResult(
        harness_id="h", available=True, cause=None, degradations=()
    )
    assert HarnessHealthResult.unprobed("h") == reported_health("h", None)


def test_capability_snapshot_unprobed_is_available() -> None:
    """The registration push reports what the diagnostics route reports: an evaluated result as-is."""
    evaluated = HarnessHealthResult(
        harness_id="h", available=False, cause=HarnessHealthCause.MISSING_BINARY, degradations=()
    )
    assert reported_health("h", evaluated) is evaluated


def test_unhealthy_skip_uses_reported_health() -> None:
    """Only an evaluation that withholds availability reads unavailable; none at all reads available."""
    withheld = evaluate_harness_health(_evidence(selftest=True))
    assert reported_health("h", withheld).available is False
    assert reported_health("h", None).available is True


def test_admission_verdict_tri_state() -> None:
    assert admission_verdict(None, _RANGE) is None
    assert admission_verdict("1.5.0", None) is False
    assert admission_verdict("1.5.0", _RANGE) is True
    assert admission_verdict("2.0.0", _RANGE) is False


def test_version_admitted_refuses_prerelease_and_unparsable() -> None:
    assert version_admitted("1.5.0", _RANGE)
    assert not version_admitted("1.5.0-beta.1", _RANGE)
    assert not version_admitted("1.5.0rc1", _RANGE)
    assert not version_admitted("not-a-version", _RANGE)


def test_select_reference_corpus_never_above_observed() -> None:
    corpus = ("1.0.0", "1.5.0", "1.9.0", "2.1.0")
    assert select_reference_corpus("1.6.0", corpus, _RANGE) == "1.5.0"
    assert select_reference_corpus("1.9.0", corpus, _RANGE) == "1.9.0"
    assert select_reference_corpus("0.9.0", corpus, _RANGE) is None
    assert select_reference_corpus("2.5.0", corpus, _RANGE) == "1.9.0"
    assert select_reference_corpus("garbage", corpus, _RANGE) is None


def test_classification_of_manifest() -> None:
    assert classification_of_manifest({"live_evidence": {"classification": "degraded"}}) is (
        CompatibilityClassification.DEGRADED
    )
    assert classification_of_manifest({"live_evidence": {"classification": "unheard-of"}}) is None
    assert classification_of_manifest({"live_evidence": "degraded"}) is None
    assert classification_of_manifest(["degraded"]) is None


def test_selftest_failed_evidence() -> None:
    assert selftest_failed(None) is None
    assert selftest_failed(LatestSelfTestResult("h", "failed", "boom", _AT)) is True
    assert selftest_failed(LatestSelfTestResult("h", "passed", None, _AT)) is False


def test_failed_selftest_survives_version_change() -> None:
    """The recorded failure is the evidence, not the version it ran against: an upgraded harness
    stays withheld until a later passing run supersedes the failure."""
    failed = LatestSelfTestResult("h", "failed", "boom", _AT)
    upgraded = evaluate_harness_health(_evidence(selftest=selftest_failed(failed)))
    assert upgraded.available is False
    assert upgraded.cause is HarnessHealthCause.SELFTEST_FAILURE
    rerun = LatestSelfTestResult("h", "passed", None, _AT)
    assert evaluate_harness_health(_evidence(selftest=selftest_failed(rerun))).available is True


def test_from_probe_report_refuses_blank_version() -> None:
    with pytest.raises(CompatibilityContractError, match="did not report an observed version"):
        CompatibilityReport.from_probe_report("  ", "<2.0", True, ())
    with pytest.raises(CompatibilityContractError, match="did not report its admitted range"):
        CompatibilityReport.from_probe_report("1.0.0", "", True, ())
    with pytest.raises(CompatibilityContractError, match="did not report a version-admitted verdict"):
        CompatibilityReport.from_probe_report("1.0.0", "<2.0", "yes", ())


def test_require_published_refuses_missing_snapshot() -> None:
    snapshot = Path("/runtime/harness-config/snapshots/abc")
    assert require_published(Path("/bundle"), snapshot) == snapshot
    with pytest.raises(HarnessBundleNotPublished, match="no snapshot is published; restart the runner"):
        require_published(Path("/bundle"), None)


def test_autonomy_source_precedence() -> None:
    overriding = HarnessSections((ClaudeCodeSection(permission_mode="acceptEdits"),))
    plain = HarnessSections((ClaudeCodeSection(),))
    assert effective_autonomy_source(overriding, True) == "legacy harness_permission_mode"
    assert effective_autonomy_source(plain, True) == "[harness] autonomy"
    assert effective_autonomy_source(plain, False) == "default"
