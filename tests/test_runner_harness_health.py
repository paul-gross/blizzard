"""The harness-health evaluator's pure policy (blizzard#438) — one case per cause, the
priority ordering across simultaneous failures, and the explicit non-failure decisions the
plan calls out: a never-run selftest, a binding declaring no version range at all, and a
corpus-free binding's own admitted-but-unclassifiable carve-out (blizzard#606, D4)."""

from __future__ import annotations

import pytest

from blizzard.runner.harness.compatibility import CompatibilityClassification, CompatibilityProbe
from blizzard.runner.harness.health import (
    DeclaredDegradation,
    HarnessHealthCause,
    HarnessHealthEvidence,
    HarnessHealthResult,
    evaluate_harness_health,
)

pytestmark = pytest.mark.unit

_HARNESS_ID = "opencode"


def _evidence(**overrides: object) -> HarnessHealthEvidence:
    """A fully-healthy baseline, one field overridden per case."""
    defaults: dict[str, object] = {
        "harness_id": _HARNESS_ID,
        "binary_present": True,
        "version_declared": True,
        "version_admitted": True,
        "version_classification": CompatibilityClassification.SUPPORTED,
        "authenticated": True,
        "unmapped_tiers": (),
        "selftest_failed": None,
        "degradations": (),
    }
    defaults.update(overrides)
    return HarnessHealthEvidence(**defaults)  # type: ignore[arg-type]


def test_healthy_evidence_is_available_with_no_cause() -> None:
    result = evaluate_harness_health(_evidence())
    assert result == HarnessHealthResult(harness_id=_HARNESS_ID, available=True, cause=None, degradations=())


def test_missing_binary_is_unavailable() -> None:
    result = evaluate_harness_health(_evidence(binary_present=False))
    assert result.available is False
    assert result.cause is HarnessHealthCause.MISSING_BINARY


def test_incompatible_version_is_unavailable() -> None:
    """An admitted version the corpus itself classifies `blocking`."""
    result = evaluate_harness_health(_evidence(version_classification=CompatibilityClassification.BLOCKING))
    assert result.available is False
    assert result.cause is HarnessHealthCause.INCOMPATIBLE_VERSION


def test_a_non_admitted_version_is_incompatible_regardless_of_classification() -> None:
    """D2: membership is checked before classification — a `version_admitted=False` version
    is `incompatible_version` even when its (irrelevant, possibly stray) corpus entry would
    otherwise classify `supported`."""
    result = evaluate_harness_health(
        _evidence(version_admitted=False, version_classification=CompatibilityClassification.SUPPORTED)
    )
    assert result.available is False
    assert result.cause is HarnessHealthCause.INCOMPATIBLE_VERSION


def test_unknown_version_is_unavailable() -> None:
    """An admitted version the corpus cannot classify at all (no manifest, or a malformed
    or unrecognized one) — distinct from a non-admitted version, which is
    `incompatible_version` instead (D2)."""
    result = evaluate_harness_health(_evidence(version_classification=None))
    assert result.available is False
    assert result.cause is HarnessHealthCause.UNKNOWN_VERSION


def test_no_version_observed_at_all_is_unknown_not_incompatible() -> None:
    """`version_admitted=None` (nothing was observed to judge membership of) reads the same
    as an admitted-but-unclassifiable version, never as a non-admitted one."""
    result = evaluate_harness_health(_evidence(version_admitted=None, version_classification=None))
    assert result.available is False
    assert result.cause is HarnessHealthCause.UNKNOWN_VERSION


def test_authentication_failure_is_unavailable() -> None:
    result = evaluate_harness_health(_evidence(authenticated=False))
    assert result.available is False
    assert result.cause is HarnessHealthCause.AUTHENTICATION_FAILURE


def test_unmapped_tier_is_unavailable() -> None:
    result = evaluate_harness_health(_evidence(unmapped_tiers=("blizzard:frontier",)))
    assert result.available is False
    assert result.cause is HarnessHealthCause.UNMAPPED_TIER


def test_selftest_failure_is_unavailable() -> None:
    result = evaluate_harness_health(_evidence(selftest_failed=True))
    assert result.available is False
    assert result.cause is HarnessHealthCause.SELFTEST_FAILURE


def test_declared_degradation_is_informational_only() -> None:
    """A degradation never makes the result unavailable, including when it stems from
    the version classification itself reading `DEGRADED` rather than `SUPPORTED`."""
    degradation = DeclaredDegradation(probe=CompatibilityProbe.ROOT_HOOK, summary="no portable hook signal")
    result = evaluate_harness_health(
        _evidence(version_classification=CompatibilityClassification.DEGRADED, degradations=(degradation,))
    )
    assert result.available is True
    assert result.cause is HarnessHealthCause.DECLARED_DEGRADATION
    assert result.degradations == (degradation,)


def test_never_run_selftest_is_not_a_failure() -> None:
    """`None` (never run) is unresolved, not a failure — only an explicit `True` withholds
    availability; `False` also passes."""
    assert evaluate_harness_health(_evidence(selftest_failed=None)).available is True
    assert evaluate_harness_health(_evidence(selftest_failed=False)).available is True


def test_a_corpus_free_binding_admitted_with_no_classification_is_available() -> None:
    """D4: a binding declaring `corpus_backed=False` (Claude Code, blizzard#606) is never
    taken unavailable over a missing classification — only over admission itself."""
    result = evaluate_harness_health(
        _evidence(harness_id="claude_code", corpus_backed=False, version_classification=None)
    )
    assert result.available is True
    assert result.cause is None


def test_a_corpus_free_binding_with_nothing_observed_is_still_unknown_version() -> None:
    """D4: `version_admitted=None` still withholds availability even with no corpus behind
    the binding — the corpus-free carve-out only ever excuses a missing classification, never
    a version that was never observed or couldn't be normalized at all."""
    result = evaluate_harness_health(
        _evidence(
            harness_id="claude_code",
            corpus_backed=False,
            version_admitted=None,
            version_classification=None,
        )
    )
    assert result.available is False
    assert result.cause is HarnessHealthCause.UNKNOWN_VERSION


def test_a_binding_declaring_no_version_range_has_no_version_cause() -> None:
    """`version_declared=False` skips both version checks entirely — an inconsistent
    `version_classification` (here, `BLOCKING`) is never consulted. No binding declares
    this today (blizzard#606), but the evaluator still supports one that might."""
    result = evaluate_harness_health(
        _evidence(
            version_declared=False,
            version_admitted=None,
            version_classification=CompatibilityClassification.BLOCKING,
        )
    )
    assert result.available is True
    assert result.cause is None


@pytest.mark.parametrize(
    ("overrides", "expected_cause"),
    [
        (
            {"binary_present": False, "authenticated": False, "selftest_failed": True},
            HarnessHealthCause.MISSING_BINARY,
        ),
        (
            {"version_classification": CompatibilityClassification.BLOCKING, "authenticated": False},
            HarnessHealthCause.INCOMPATIBLE_VERSION,
        ),
        (
            {
                "version_admitted": False,
                "version_classification": CompatibilityClassification.SUPPORTED,
                "authenticated": False,
            },
            HarnessHealthCause.INCOMPATIBLE_VERSION,
        ),
        (
            {"version_classification": None, "authenticated": False},
            HarnessHealthCause.UNKNOWN_VERSION,
        ),
        (
            {"authenticated": False, "unmapped_tiers": ("blizzard:frontier",)},
            HarnessHealthCause.AUTHENTICATION_FAILURE,
        ),
        (
            {"unmapped_tiers": ("blizzard:frontier",), "selftest_failed": True},
            HarnessHealthCause.UNMAPPED_TIER,
        ),
        (
            {"selftest_failed": True, "degradations": (DeclaredDegradation(CompatibilityProbe.USAGE_COST, "x"),)},
            HarnessHealthCause.SELFTEST_FAILURE,
        ),
    ],
)
def test_priority_order_reports_only_the_highest_priority_cause(
    overrides: dict[str, object], expected_cause: HarnessHealthCause
) -> None:
    result = evaluate_harness_health(_evidence(**overrides))
    assert result.available is False
    assert result.cause is expected_cause
