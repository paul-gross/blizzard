"""The offline contract for a pinned coding-harness compatibility proof.

Harness-neutral vocabulary only: a probe supplies its pinned version and its observations through
``ICompatibilityProbe``, and this module closes the roster and classifies them. Every
harness-specific binding, the pinned version included, lives under ``harness/internal``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class CompatibilityProbe(StrEnum):
    """The complete set of external contracts the proof must account for."""

    FRESH_TURN = "fresh_turn"
    RESUME = "resume"
    PROCESS_CONTROL = "process_control"
    JUDGEMENT = "judgement"
    ROOT_HOOK = "root_hook"
    PERMISSION = "permission"
    MODEL_VARIANT = "model_variant"
    USAGE_COST = "usage_cost"
    TAKEOVER = "takeover"
    TRANSCRIPT_READ = "transcript_read"
    TRANSCRIPT_CURSOR = "transcript_cursor"
    CHILD_SESSIONS = "child_sessions"
    CONFIGURATION_ISOLATION = "configuration_isolation"


# The ordered tuple is the report, diagnostic, and fixture contract; adding a probe is intentionally
# a closure change because a report that omits it cannot be complete.
PROBE_ROSTER: tuple[CompatibilityProbe, ...] = (
    CompatibilityProbe.FRESH_TURN,
    CompatibilityProbe.RESUME,
    CompatibilityProbe.PROCESS_CONTROL,
    CompatibilityProbe.JUDGEMENT,
    CompatibilityProbe.ROOT_HOOK,
    CompatibilityProbe.PERMISSION,
    CompatibilityProbe.MODEL_VARIANT,
    CompatibilityProbe.USAGE_COST,
    CompatibilityProbe.TAKEOVER,
    CompatibilityProbe.TRANSCRIPT_READ,
    CompatibilityProbe.TRANSCRIPT_CURSOR,
    CompatibilityProbe.CHILD_SESSIONS,
    CompatibilityProbe.CONFIGURATION_ISOLATION,
)

# String form is useful at the JSON/fixture boundary and keeps the closed roster easy to inspect.
REQUIRED_PROBES: tuple[str, ...] = tuple(probe.value for probe in PROBE_ROSTER)


class CompatibilityContractError(ValueError):
    """The supplied observations cannot form a complete compatibility report."""


class UnknownProbeError(CompatibilityContractError):
    """An observation named a probe outside the closed roster."""


class IncompleteProbeReportError(CompatibilityContractError):
    """A report omitted a required probe or supplied one more than once."""


class EvidenceState(StrEnum):
    """The deterministic states a live probe may report before policy classifies them."""

    OBSERVED = "observed"
    ABSENT = "absent"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class CompatibilityClassification(StrEnum):
    """The three outcomes a compatibility report can expose."""

    SUPPORTED = "supported"
    DEGRADED = "degraded"
    BLOCKING = "blocking"


# These are the only absences that already have an honest harness-neutral representation.  A
# missing correctness signal is blocking; it is never made harmless by a caller's description.
DEGRADABLE_ABSENCES: frozenset[CompatibilityProbe] = frozenset(
    {
        CompatibilityProbe.ROOT_HOOK,
        CompatibilityProbe.USAGE_COST,
        CompatibilityProbe.CHILD_SESSIONS,
    }
)


def _probe(value: CompatibilityProbe | str) -> CompatibilityProbe:
    if isinstance(value, CompatibilityProbe):
        return value
    try:
        return CompatibilityProbe(value)
    except ValueError as exc:
        raise UnknownProbeError(f"unknown compatibility probe: {value!r}") from exc


def _state(value: EvidenceState | str) -> EvidenceState:
    if isinstance(value, EvidenceState):
        return value
    try:
        return EvidenceState(value)
    except ValueError as exc:
        raise CompatibilityContractError(f"unknown compatibility evidence state: {value!r}") from exc


@dataclass(frozen=True)
class ProbeObservation:
    """Raw observation: ``observed`` is success, ``absent`` is deliberate neutral absence, and failed or ambiguous
    states are never degradable. Evidence contains sanitized paths or shape names, not provider output."""

    probe: CompatibilityProbe | str
    state: EvidenceState | str
    summary: str
    evidence: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "probe", _probe(self.probe))
        object.__setattr__(self, "state", _state(self.state))
        evidence = (self.evidence,) if isinstance(self.evidence, str) else tuple(self.evidence)
        object.__setattr__(self, "evidence", evidence)
        if not self.summary.strip():
            raise CompatibilityContractError(f"probe {_probe(self.probe).value!r} has no summary")
        if any(not item.strip() for item in self.evidence):
            raise CompatibilityContractError(f"probe {_probe(self.probe).value!r} has blank evidence")

    @classmethod
    def observed(cls, probe: CompatibilityProbe | str, summary: str, *evidence: str) -> ProbeObservation:
        return cls(probe, EvidenceState.OBSERVED, summary, tuple(evidence))

    @classmethod
    def absent(cls, probe: CompatibilityProbe | str, summary: str, *evidence: str) -> ProbeObservation:
        return cls(probe, EvidenceState.ABSENT, summary, tuple(evidence))

    @classmethod
    def failed(cls, probe: CompatibilityProbe | str, summary: str, *evidence: str) -> ProbeObservation:
        return cls(probe, EvidenceState.FAILED, summary, tuple(evidence))

    @classmethod
    def ambiguous(cls, probe: CompatibilityProbe | str, summary: str, *evidence: str) -> ProbeObservation:
        return cls(probe, EvidenceState.AMBIGUOUS, summary, tuple(evidence))


def classify_observation(observation: ProbeObservation) -> CompatibilityClassification:
    """Apply the closed absence policy to one observation, without any external work."""

    probe = _probe(observation.probe)
    state = _state(observation.state)
    if state is EvidenceState.OBSERVED:
        return CompatibilityClassification.SUPPORTED
    if state is EvidenceState.ABSENT and probe in DEGRADABLE_ABSENCES:
        return CompatibilityClassification.DEGRADED
    # Failed and ambiguous results are blocking, as is absence of a contract that participates in
    # process, permission, transcript, or configuration correctness.
    return CompatibilityClassification.BLOCKING


@dataclass(frozen=True)
class ProbeResult:
    """One classified probe result retained in the complete report."""

    probe: CompatibilityProbe
    state: EvidenceState
    classification: CompatibilityClassification
    summary: str
    evidence: tuple[str, ...]

    @classmethod
    def from_observation(cls, observation: ProbeObservation) -> ProbeResult:
        probe = _probe(observation.probe)
        state = _state(observation.state)
        return cls(
            probe=probe,
            state=state,
            classification=classify_observation(observation),
            summary=observation.summary,
            evidence=tuple(observation.evidence),
        )


@dataclass(frozen=True)
class CompatibilityReport:
    """A complete, ordered compatibility report for one observed harness version."""

    observed_version: str
    expected_version: str
    results: tuple[ProbeResult, ...]
    version_matches_pin: bool

    @classmethod
    def from_observations(
        cls,
        observed_version: str,
        expected_version: str,
        observations: Iterable[ProbeObservation],
    ) -> CompatibilityReport:
        if not isinstance(observed_version, str) or not observed_version.strip():
            raise CompatibilityContractError("the observed harness version is empty")
        if not isinstance(expected_version, str) or not expected_version.strip():
            raise CompatibilityContractError("the expected harness version is empty")
        collected: dict[CompatibilityProbe, ProbeResult] = {}
        for observation in observations:
            result = ProbeResult.from_observation(observation)
            if result.probe in collected:
                raise IncompleteProbeReportError(f"duplicate compatibility probe: {result.probe.value}")
            collected[result.probe] = result

        missing = [probe.value for probe in PROBE_ROSTER if probe not in collected]
        if missing:
            raise IncompleteProbeReportError(f"compatibility report is missing probes: {', '.join(missing)}")
        return cls(
            observed_version=observed_version,
            expected_version=expected_version,
            results=tuple(collected[probe] for probe in PROBE_ROSTER),
            version_matches_pin=observed_version == expected_version,
        )

    @property
    def classification(self) -> CompatibilityClassification:
        """The worst deterministic result, with a version mismatch always blocking."""

        if not self.version_matches_pin or any(
            result.classification is CompatibilityClassification.BLOCKING for result in self.results
        ):
            return CompatibilityClassification.BLOCKING
        if any(result.classification is CompatibilityClassification.DEGRADED for result in self.results):
            return CompatibilityClassification.DEGRADED
        return CompatibilityClassification.SUPPORTED

    @property
    def complete(self) -> bool:
        """Reports can only be constructed after every roster member appears exactly once."""

        return len(self.results) == len(PROBE_ROSTER) and tuple(result.probe for result in self.results) == PROBE_ROSTER

    @property
    def admissible(self) -> bool:
        """Whether later production work may depend on this pinned observation."""

        return self.complete and self.classification is not CompatibilityClassification.BLOCKING

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        reasons = []
        if not self.version_matches_pin:
            reasons.append(f"observed {self.observed_version!r}, expected {self.expected_version!r}")
        reasons.extend(
            result.probe.value
            for result in self.results
            if result.classification is CompatibilityClassification.BLOCKING
        )
        return tuple(reasons)

    def to_payload(self) -> dict[str, object]:
        """Render stable report data for a later diagnostic without exposing raw observations."""

        return {
            "observed_version": self.observed_version,
            "classification": self.classification.value,
            "complete": self.complete,
            "admissible": self.admissible,
            "probes": [
                {
                    "name": result.probe.value,
                    "state": result.state.value,
                    "classification": result.classification.value,
                    "summary": result.summary,
                    "evidence": list(result.evidence),
                }
                for result in self.results
            ],
        }


class ICompatibilityProbe(Protocol):
    """The inward-facing boundary for a live compatibility proof."""

    observed_version: str
    expected_version: str

    def run(self) -> Sequence[ProbeObservation]:
        """Collect one observation for each member of :data:`PROBE_ROSTER`."""

        ...


@dataclass(frozen=True)
class CompatibilityDiagnostic:
    """Construct complete reports without owning process, filesystem, or provider work.

    A concrete probe supplies collaborators at the outer binding.
    """

    probe: ICompatibilityProbe

    def run(self) -> CompatibilityReport:
        """Run the injected probe and reject anything that cannot form a complete report."""

        observations = self.probe.run()
        try:
            version = self.probe.observed_version
            expected = self.probe.expected_version
        except AttributeError as exc:
            raise CompatibilityContractError("the compatibility probe did not report both versions") from exc
        if not isinstance(version, str) or not version.strip():
            raise CompatibilityContractError("the compatibility probe did not report an observed version")
        if not isinstance(expected, str) or not expected.strip():
            raise CompatibilityContractError("the compatibility probe did not report an expected version")
        return CompatibilityReport.from_observations(version, expected, observations)


SUPPORTED = CompatibilityClassification.SUPPORTED
DEGRADED = CompatibilityClassification.DEGRADED
BLOCKING = CompatibilityClassification.BLOCKING

__all__ = [
    "BLOCKING",
    "DEGRADABLE_ABSENCES",
    "DEGRADED",
    "PROBE_ROSTER",
    "REQUIRED_PROBES",
    "SUPPORTED",
    "CompatibilityClassification",
    "CompatibilityContractError",
    "CompatibilityDiagnostic",
    "CompatibilityProbe",
    "CompatibilityReport",
    "EvidenceState",
    "ICompatibilityProbe",
    "IncompleteProbeReportError",
    "ProbeObservation",
    "ProbeResult",
    "UnknownProbeError",
    "classify_observation",
]
