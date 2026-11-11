"""Interpret durable observations of a revision with no CI checks."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.domain.actions.effects import ActionEscalation
from blizzard.hub.domain.artifact.model import StoredArtifact

PREFIX = "empty-checks/"
GRACE = timedelta(minutes=10)


def marker_name(repository: str, sha: str) -> str:
    return f"{PREFIX}{repository}/{sha}"


def marker_content(repo: str, policy: str, sha: str, now: datetime) -> str:
    return json.dumps({"repo": repo, "policy": policy, "sha": sha, "at": now.isoformat()})


def observation(markers: tuple[StoredArtifact, ...], epoch: int, repository: str, sha: str) -> datetime | None:
    name = marker_name(repository, sha)
    return next(
        (
            datetime.fromisoformat(json.loads(row.data)["at"])
            for row in markers
            if row.epoch == epoch and row.name == name
        ),
        None,
    )


def missing_checks(repo: str, policy: str, sha: str, at: datetime, *, timed_out: bool = False) -> ActionEscalation:
    evidence = f"No checks were observed at {at.isoformat()} on SHA {sha}; " + (
        "the overall deadline expired and the observation was not reconfirmed"
        if timed_out
        else "checks are still absent after the ten-minute grace period"
    )
    return ActionEscalation(
        EscalationCause.MISSING_CHECKS,
        f"{repo} ({policy}): {evidence}. Configure CI checks, or explicitly enable allow_no_ci "
        "for this repository if it intentionally has no CI.",
    )


def timeout_observation(markers: tuple[StoredArtifact, ...], node_id: str, epoch: int) -> ActionEscalation | None:
    for row in markers:
        if row.node_id == node_id and row.epoch == epoch and row.name.startswith(PREFIX):
            data = json.loads(row.data)
            return missing_checks(
                data["repo"], data["policy"], data["sha"], datetime.fromisoformat(data["at"]), timed_out=True
            )
    return None
