"""What the runner submits at the end of a node-step — its completion, or a runner-configured gate
in place of one — and what the hub's apply answers. The hub client maps each to and from the wire
(``bzh:data-roles``)."""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import ApplyOutcome
from blizzard.foundation.roles import domain_model
from blizzard.runner.node_steps.envelope import Envelope


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
class CheckVerdict:
    """One check's runner-executed outcome — its command and whether it passed; the output
    tail stays runner-local."""

    command: str
    passed: bool


@domain_model
@dataclass(frozen=True)
class Completion:
    """A node-step's completion — the judgement's choice, the checks, and the artifacts, fenced
    by the executing lease's ``epoch``. ``decision_id`` names the gate decision a resolving
    transition carries; ``route_token`` and ``lease_id`` are the route and owning-lease checks."""

    choice: str
    epoch: int
    from_node_id: str
    check_results: list[CheckVerdict] = field(default_factory=list)
    artifacts: list[CompletionArtifact] = field(default_factory=list)
    decision_id: str | None = None
    route_token: str | None = None
    lease_id: str | None = None


@domain_model
@dataclass(frozen=True)
class GateSubmission:
    """A runner-configured gate: a decision submitted in place of a transition, carrying the
    gated step's artifacts under its fencing ``epoch``."""

    from_node_id: str
    epoch: int
    artifacts: list[CompletionArtifact] = field(default_factory=list)
    route_token: str | None = None
    lease_id: str | None = None


@domain_model
@dataclass(frozen=True)
class ApplyReply:
    """The hub's answer to a completion or a gate submission: ``next_envelope`` is set when
    ``outcome`` is ``NEXT``, ``detail`` on any other outcome."""

    outcome: ApplyOutcome
    next_envelope: Envelope | None = None
    detail: str | None = None
