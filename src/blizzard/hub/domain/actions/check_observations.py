"""Interpret durable observations of a revision with no CI checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.domain.actions.effects import ActionEscalation
from blizzard.hub.domain.artifact.model import StoredArtifact

PREFIX = "empty-checks/"
GRACE = timedelta(minutes=10)


class Eligibility(Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    RECORD = "record"
    WAIT = "wait"
    ESCALATE = "escalate"


@domain_model
@dataclass(frozen=True)
class EligibilityDecision:
    outcome: Eligibility
    first: datetime | None = None


def eligibility(
    *,
    green: bool,
    empty: bool,
    allow_no_ci: bool,
    markers: tuple[StoredArtifact, ...],
    epoch: int,
    repository: str,
    sha: str,
    now: datetime,
) -> EligibilityDecision:
    """Decide eligibility and the empty-check grace from the current read and captured terms."""
    if green:
        return EligibilityDecision(Eligibility.ELIGIBLE)
    if not empty:
        return EligibilityDecision(Eligibility.INELIGIBLE)
    if allow_no_ci:
        return EligibilityDecision(Eligibility.ELIGIBLE)
    first = observation(markers, epoch, repository, sha)
    if first is None:
        return EligibilityDecision(Eligibility.RECORD)
    if as_utc(now) - as_utc(first) >= GRACE:
        return EligibilityDecision(Eligibility.ESCALATE, first)
    return EligibilityDecision(Eligibility.WAIT)


def marker_name(repository: str, sha: str) -> str:
    return f"{PREFIX}{repository}/{sha}"


def marker_content(repo: str, policy: str, sha: str, now: datetime) -> str:
    return json.dumps({"repo": repo, "policy": policy, "sha": sha, "at": iso_utc(now)})


def observation(markers: tuple[StoredArtifact, ...], epoch: int, repository: str, sha: str) -> datetime | None:
    name = marker_name(repository, sha)
    return next(
        (
            as_utc(datetime.fromisoformat(json.loads(row.data)["at"]))
            for row in markers
            if row.epoch == epoch and row.name == name
        ),
        None,
    )


def missing_checks(repo: str, policy: str, sha: str, at: datetime, *, timed_out: bool = False) -> ActionEscalation:
    evidence = f"No checks were observed at {iso_utc(at)} on SHA {sha}; " + (
        "the overall deadline expired and the observation was not reconfirmed"
        if timed_out
        else "checks are still absent after the ten-minute grace period"
    )
    return ActionEscalation(
        EscalationCause.MISSING_CHECKS,
        f"{repo} ({policy}): {evidence}. The captured landing terms are strict (allow_no_ci=false). "
        "Configure CI checks, or explicitly enable allow_no_ci for this repository if it intentionally has no CI, "
        "then requeue to capture the new terms.",
    )


def timeout_observation(markers: tuple[StoredArtifact, ...], node_id: str, epoch: int) -> ActionEscalation | None:
    for row in markers:
        if row.node_id == node_id and row.epoch == epoch and row.name.startswith(PREFIX):
            data = json.loads(row.data)
            return missing_checks(
                data["repo"], data["policy"], data["sha"], datetime.fromisoformat(data["at"]), timed_out=True
            )
    return None
