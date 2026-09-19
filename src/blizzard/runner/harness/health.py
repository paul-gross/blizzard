"""The harness-health evaluation policy (blizzard#438).

A pure, dependency-free closure over already-collected evidence — no I/O, no subprocess, no
clock read happens here. The evaluator answers one question, "is this configured harness
binding actually usable," from facts a probe seam (:class:`~blizzard.runner.harness.adapter.
IHarnessHealthProbe`) gathered ahead of time."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from blizzard.runner.harness.compatibility import CompatibilityClassification, CompatibilityProbe


class HarnessHealthCause(StrEnum):
    """Every reason a configured harness binding can be withheld from availability,
    in the priority order :func:`evaluate_harness_health` checks them — the first
    match wins, so a binding failing several checks at once reports only the
    highest-priority one."""

    MISSING_BINARY = "missing_binary"
    INCOMPATIBLE_VERSION = "incompatible_version"
    UNKNOWN_VERSION = "unknown_version"
    AUTHENTICATION_FAILURE = "authentication_failure"
    UNMAPPED_TIER = "unmapped_tier"
    SELFTEST_FAILURE = "selftest_failure"
    DECLARED_DEGRADATION = "declared_degradation"


@dataclass(frozen=True)
class DeclaredDegradation:
    """One known, non-blocking compatibility gap a harness binding declares about
    itself — reported diagnostics only (never a cause of unavailability on its own)."""

    probe: CompatibilityProbe
    summary: str

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError(f"declared degradation for probe {self.probe.value!r} has no summary")


@dataclass(frozen=True)
class HarnessHealthEvidence:
    """The evaluator's whole input — every fact :func:`evaluate_harness_health` needs,
    already collected by an :class:`~blizzard.runner.harness.adapter.IHarnessHealthProbe`
    and whatever selftest/tier-resolution facts the caller already holds. Evaluation is a
    pure function of this dataclass alone."""

    harness_id: str
    binary_present: bool
    #: Whether this binding declares a supported-version range at all; absent is not itself a failure.
    version_declared: bool
    #: Meaningful only when declared; ``None`` means no observed version matched the fixture corpus.
    version_classification: CompatibilityClassification | None
    authenticated: bool
    #: Non-empty means a tier this runner is configured for that this harness can't actually resolve.
    unmapped_tiers: tuple[str, ...]
    #: ``None`` (never run) and ``False`` both pass; only a recorded failure withholds availability.
    selftest_failed: bool | None
    degradations: tuple[DeclaredDegradation, ...] = ()


@dataclass(frozen=True)
class HarnessHealthResult:
    """One evaluation's outcome: whether the binding is available, the single cause when
    it is not, and every declared degradation regardless — reported informationally even
    when the binding is otherwise unavailable for an unrelated reason."""

    harness_id: str
    available: bool
    cause: HarnessHealthCause | None
    degradations: tuple[DeclaredDegradation, ...]


def evaluate_harness_health(evidence: HarnessHealthEvidence) -> HarnessHealthResult:
    """Apply the closed priority policy to one binding's already-collected evidence.

    First match wins, in :class:`HarnessHealthCause`'s own declared order: missing binary,
    then version, then authentication, and so on. A declared degradation never makes the
    result unavailable, surfaced only once every prior check passes."""

    if not evidence.binary_present:
        return _unavailable(evidence, HarnessHealthCause.MISSING_BINARY)
    if evidence.version_declared:
        if evidence.version_classification is CompatibilityClassification.BLOCKING:
            return _unavailable(evidence, HarnessHealthCause.INCOMPATIBLE_VERSION)
        if evidence.version_classification is None:
            return _unavailable(evidence, HarnessHealthCause.UNKNOWN_VERSION)
    if not evidence.authenticated:
        return _unavailable(evidence, HarnessHealthCause.AUTHENTICATION_FAILURE)
    if evidence.unmapped_tiers:
        return _unavailable(evidence, HarnessHealthCause.UNMAPPED_TIER)
    if evidence.selftest_failed is True:
        return _unavailable(evidence, HarnessHealthCause.SELFTEST_FAILURE)
    cause = HarnessHealthCause.DECLARED_DEGRADATION if evidence.degradations else None
    return HarnessHealthResult(
        harness_id=evidence.harness_id, available=True, cause=cause, degradations=evidence.degradations
    )


def _unavailable(evidence: HarnessHealthEvidence, cause: HarnessHealthCause) -> HarnessHealthResult:
    return HarnessHealthResult(
        harness_id=evidence.harness_id, available=False, cause=cause, degradations=evidence.degradations
    )


__all__ = [
    "DeclaredDegradation",
    "HarnessHealthCause",
    "HarnessHealthEvidence",
    "HarnessHealthResult",
    "evaluate_harness_health",
]
