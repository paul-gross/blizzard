"""The harness-health evaluation policy.

A pure, dependency-free closure over already-collected evidence — no I/O, no subprocess, no
clock read happens here. The evaluator answers one question, "is this configured harness
binding actually usable," from facts a probe seam (:class:`~blizzard.runner.harness.adapter.
IHarnessHealthProbe`) gathered ahead of time."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.compatibility import CompatibilityClassification, CompatibilityProbe


class HarnessHealthCause(StrEnum):
    """Every reason a configured harness binding's health can carry a ``cause`` at all, in
    the priority order :func:`evaluate_harness_health` checks them — the first match wins,
    so a binding failing several checks at once reports only the highest-priority one. Every
    member but ``DECLARED_DEGRADATION`` withholds availability; that one reports precisely
    when the binding *is* available, but degraded."""

    MISSING_BINARY = "missing_binary"
    INCOMPATIBLE_VERSION = "incompatible_version"
    UNKNOWN_VERSION = "unknown_version"
    AUTHENTICATION_FAILURE = "authentication_failure"
    CONFIG_CONFLICT = "config_conflict"
    UNMAPPED_TIER = "unmapped_tier"
    SELFTEST_FAILURE = "selftest_failure"
    DECLARED_DEGRADATION = "declared_degradation"


@domain_model
@dataclass(frozen=True)
class DeclaredDegradation:
    """One known, non-blocking compatibility gap a harness binding declares about
    itself — reported diagnostics only (never a cause of unavailability on its own)."""

    probe: CompatibilityProbe
    summary: str

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError(f"declared degradation for probe {self.probe.value!r} has no summary")


@domain_model
@dataclass(frozen=True)
class HarnessHealthEvidence:
    """The whole input of :func:`evaluate_harness_health`, which is a pure function of it alone."""

    harness_id: str
    binary_present: bool
    #: Whether this binding declares a supported-version range at all; absent is not itself a failure.
    version_declared: bool
    #: Meaningful only when declared: membership per the caller; ``None`` means nothing was observed.
    version_admitted: bool | None
    #: Meaningful only when declared and admitted: ``None`` means the corpus fixture couldn't classify it.
    version_classification: CompatibilityClassification | None
    authenticated: bool
    #: Non-empty means a tier this runner is configured for that this harness can't actually resolve.
    unmapped_tiers: tuple[str, ...]
    #: ``None`` (never run) and ``False`` both pass; only a recorded failure withholds availability.
    selftest_failed: bool | None
    #: Whether this binding backs its admitted range with a committed corpus; defaults `True` (fail closed).
    corpus_backed: bool = True
    degradations: tuple[DeclaredDegradation, ...] = ()
    #: Ambient settings defeating the runner's wiring, as file and key; non-empty withholds availability.
    config_conflicts: tuple[str, ...] = ()


@domain_model
@dataclass(frozen=True)
class HarnessHealthResult:
    """One evaluation's outcome: whether the binding is available, the single cause —
    ``declared_degradation`` when available but degraded, one of the withholding causes
    when not, ``None`` when neither — and every declared degradation regardless, reported
    informationally even when the binding is unavailable for an unrelated reason."""

    harness_id: str
    available: bool
    cause: HarnessHealthCause | None
    degradations: tuple[DeclaredDegradation, ...]

    @classmethod
    def unprobed(cls, harness_id: str) -> HarnessHealthResult:
        """The health a binding reports before any evaluation has run for it: available, with
        no cause and no degradations. Health fails open — a binding is never withheld for
        lack of evidence, only for evidence against it."""
        return cls(harness_id=harness_id, available=True, cause=None, degradations=())


def reported_health(harness_id: str, result: HarnessHealthResult | None) -> HarnessHealthResult:
    """The health every reader reports for ``harness_id``: its last evaluation when one ran,
    else :meth:`HarnessHealthResult.unprobed`. The one owner of the fail-open reading — the
    diagnostics route, the registration push, and session-harness selection all defer here."""
    return result if result is not None else HarnessHealthResult.unprobed(harness_id)


def evaluate_harness_health(evidence: HarnessHealthEvidence) -> HarnessHealthResult:
    """Apply the closed priority policy to one binding's already-collected evidence: first
    match wins, in :class:`HarnessHealthCause`'s own declared order. Within the version
    check, membership is checked before classification, so a non-admitted version is
    always ``incompatible_version``; ``unknown_version`` is reserved for none observed, or
    an admitted version only a corpus-backed binding can't classify."""

    if not evidence.binary_present:
        return _unavailable(evidence, HarnessHealthCause.MISSING_BINARY)
    if evidence.version_declared:
        if evidence.version_admitted is None:
            return _unavailable(evidence, HarnessHealthCause.UNKNOWN_VERSION)
        if evidence.version_admitted is False:
            return _unavailable(evidence, HarnessHealthCause.INCOMPATIBLE_VERSION)
        if evidence.version_classification is CompatibilityClassification.BLOCKING:
            return _unavailable(evidence, HarnessHealthCause.INCOMPATIBLE_VERSION)
        if evidence.corpus_backed and evidence.version_classification is None:
            return _unavailable(evidence, HarnessHealthCause.UNKNOWN_VERSION)
    if not evidence.authenticated:
        return _unavailable(evidence, HarnessHealthCause.AUTHENTICATION_FAILURE)
    if evidence.config_conflicts:
        return _unavailable(evidence, HarnessHealthCause.CONFIG_CONFLICT)
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
    "reported_health",
]
