"""The closed roster of OpenCode 2 contract rows, and the policy that classifies each observation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.compatibility import CompatibilityClassification, EvidenceState
from blizzard.runner.harness.json_value import JsonObject


class ProofRow(StrEnum):
    MANAGED_SERVICE = "managed_service"
    CALLER_NAMED_SESSION = "caller_named_session"
    SESSION_ENVIRONMENT = "session_environment"
    PROMPT_AND_COMPLETION = "prompt_and_completion"
    SERVICE_RESTART = "service_restart"
    CONCURRENT_SESSIONS = "concurrent_sessions"
    INTERRUPT = "interrupt"
    JUDGEMENT = "judgement"
    QUEUE_AND_STEER = "queue_and_steer"
    USAGE_AND_COST = "usage_and_cost"
    PRICING = "pricing"
    ERRORS_AND_LIMITS = "errors_and_limits"
    PERMISSION_RULES = "permission_rules"
    PERMISSION_REQUESTS = "permission_requests"
    EVENTS = "events"
    CHILD_AGENTS = "child_agents"
    TRANSCRIPT_READ = "transcript_read"
    COMPACTION = "compaction"
    MODEL_AND_VARIANT = "model_and_variant"
    TAKEOVER = "takeover"
    VERSION = "version"
    STORE_MIGRATION = "store_migration"


#: The report and manifest order; a row missing from a report makes it incomplete.
ROW_ROSTER: tuple[ProofRow, ...] = tuple(ProofRow)

#: Rows whose absence the binding has an honest degraded answer for; any other absence blocks its step.
DEGRADABLE_ROWS: frozenset[ProofRow] = frozenset({ProofRow.PRICING, ProofRow.EVENTS})


@domain_model
@dataclass(frozen=True)
class Degradation:
    """A degradation the binding declares for its version, carried by the row it was observed under."""

    row: ProofRow
    summary: str


#: What OpenCode 2's binding declares whatever the proof observes: a session's settings do not reach its children.
DECLARED_DEGRADATIONS: tuple[Degradation, ...] = (
    Degradation(
        ProofRow.PERMISSION_RULES,
        "a subagent takes its agent's configured permissions until the driver applies the session's rules to it",
    ),
    Degradation(
        ProofRow.SESSION_ENVIRONMENT,
        "a subagent's tools run without the lease's environment or identity, which the driver never applies to a child",
    ),
)


@domain_model
@dataclass(frozen=True)
class RowObservation:
    """One row's raw result: ``evidence`` names the fixtures that back it."""

    row: ProofRow
    state: EvidenceState
    summary: str
    evidence: tuple[str, ...] = ()
    facts: JsonObject | None = None


def classify_row(observation: RowObservation) -> CompatibilityClassification:
    if observation.state is EvidenceState.OBSERVED:
        return CompatibilityClassification.SUPPORTED
    if observation.state is EvidenceState.ABSENT and observation.row in DEGRADABLE_ROWS:
        return CompatibilityClassification.DEGRADED
    return CompatibilityClassification.BLOCKING


class IncompleteProofError(ValueError):
    """A proof report omitted a row or recorded one twice."""


@domain_model
@dataclass(frozen=True)
class ProofReport:
    observed_version: str
    admitted_range: str
    version_admitted: bool
    observations: tuple[RowObservation, ...]

    @classmethod
    def complete(
        cls, observed_version: str, admitted_range: str, version_admitted: bool, rows: Iterable[RowObservation]
    ) -> ProofReport:
        by_row: dict[ProofRow, RowObservation] = {}
        for observation in rows:
            if observation.row in by_row:
                raise IncompleteProofError(f"row {observation.row.value} was recorded twice")
            by_row[observation.row] = observation
        missing = [row.value for row in ROW_ROSTER if row not in by_row]
        if missing:
            raise IncompleteProofError(f"the proof recorded no result for: {', '.join(missing)}")
        return cls(observed_version, admitted_range, version_admitted, tuple(by_row[row] for row in ROW_ROSTER))

    def classification(self) -> CompatibilityClassification:
        classes = [classify_row(observation) for observation in self.observations]
        if not self.version_admitted or CompatibilityClassification.BLOCKING in classes:
            return CompatibilityClassification.BLOCKING
        if CompatibilityClassification.DEGRADED in classes:
            return CompatibilityClassification.DEGRADED
        return CompatibilityClassification.SUPPORTED

    def admissible(self) -> bool:
        return self.classification() is not CompatibilityClassification.BLOCKING

    def payload(self, degradations: Sequence[Degradation] = DECLARED_DEGRADATIONS) -> JsonObject:
        return {
            "observed_version": self.observed_version,
            "admitted_range": self.admitted_range,
            "classification": self.classification().value,
            "admissible": self.admissible(),
            "rows": [
                {
                    "name": observation.row.value,
                    "state": observation.state.value,
                    "classification": classify_row(observation).value,
                    "summary": observation.summary,
                    "evidence": list(observation.evidence),
                    **({"facts": observation.facts} if observation.facts is not None else {}),
                }
                for observation in self.observations
            ],
            "declared_degradations": [
                {"row": degradation.row.value, "summary": degradation.summary} for degradation in degradations
            ],
        }


__all__ = [
    "DECLARED_DEGRADATIONS",
    "DEGRADABLE_ROWS",
    "ROW_ROSTER",
    "Degradation",
    "IncompleteProofError",
    "ProofReport",
    "ProofRow",
    "RowObservation",
    "classify_row",
]
