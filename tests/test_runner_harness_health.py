"""The harness-health evaluator's pure policy (blizzard#438) — one case per cause, the
priority ordering across simultaneous failures, and the two explicit non-failure decisions
the plan calls out: a never-run selftest, and Claude Code's own absent version declaration."""

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
    result = evaluate_harness_health(_evidence(version_classification=CompatibilityClassification.BLOCKING))
    assert result.available is False
    assert result.cause is HarnessHealthCause.INCOMPATIBLE_VERSION


def test_unknown_version_is_unavailable() -> None:
    result = evaluate_harness_health(_evidence(version_classification=None))
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


def test_claude_code_has_no_version_cause() -> None:
    """`version_declared=False` skips both version checks entirely — an inconsistent
    `version_classification` (here, `BLOCKING`) is never consulted, matching Claude Code's
    own declared-no-version-range binding."""
    result = evaluate_harness_health(
        _evidence(
            harness_id="claude_code",
            version_declared=False,
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
