"""What a runner submits at the end of a node-step — its completion, or a runner-configured gate in
place of one — as the hub's execution rules read it. The fleet controller maps the wire bodies to
these at the edge (``bzh:data-roles``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class CompletionArtifact:
    """An artifact committed atomically with the submission: a pushed ``git_commit`` pointer
    (``forge`` the worker's declared origin) or an ``asset``'s content. ``attached`` marks an
    explicit attach rather than the judgement fallback."""

    name: str
    kind: ArtifactKind
    forge: str | None = None
    repo: str | None = None
    branch_name: str | None = None
    commit_hash: str | None = None
    content: str | None = None
    attached: bool = False


@domain_model
@dataclass(frozen=True)
class CheckOutcome:
    """One deterministic check the runner executed, and whether it passed."""

    command: str
    passed: bool


@domain_model
@dataclass(frozen=True)
class Completion:
    """A node-step's completion — the judgement's choice, the checks, and the artifacts,
    fenced by the executing lease's ``epoch``. ``decision_id`` names the
    gate decision a resolving transition carries; ``route_token`` and ``lease_id`` are the
    optional route and owning-lease checks."""

    choice: str
    epoch: int
    runner_id: str
    from_node_id: str
    check_results: Sequence[CheckOutcome] = ()
    artifacts: Sequence[CompletionArtifact] = ()
    decision_id: str | None = None
    route_token: str | None = None
    lease_id: str | None = None


@domain_model
@dataclass(frozen=True)
class GateSubmission:
    """A runner-configured gate: a decision submitted in place of a transition, carrying the
    gated step's artifacts under the step's fencing ``epoch``."""

    from_node_id: str
    epoch: int
    runner_id: str
    artifacts: Sequence[CompletionArtifact] = ()
    route_token: str | None = None
    lease_id: str | None = None
