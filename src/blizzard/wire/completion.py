"""The completion submission (D-027/D-036) — a node-step's atomic, fenced write.

``POST /chunks/{id}/completions`` submits one node-step's completion: the judgement
choice the worker emitted, the check results, and the step's artifacts — **one
atomic, epoch-fenced write** (D-036). Git-commit artifacts name branches already
pushed to the forge (D-026). A stale epoch is rejected and the artifacts never
enter the store (D-007). The reply is the :class:`~blizzard.wire.envelope.ApplyResponse`.

At a human-judged node the hub rejects a plain transition-style completion (human
signoff required) — a runner-config gate posts a decision instead
(:mod:`blizzard.wire.decision`). The one exception is the **resolving transition**:
a completion whose ``decision_id`` points at a resolved decision is how the holding
runner advances a chunk past a gate once a person has decided (D-027/D-045).
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.hub.domain.artifacts import ArtifactKind


class SubmittedArtifact(BaseModel):
    """An artifact committed atomically with the completion (D-036)."""

    name: str
    kind: ArtifactKind
    # git_commit variant — the branch is pushed to the forge before submission (D-026).
    repo: str | None = None
    branch_name: str | None = None
    commit_hash: str | None = None
    # asset variant
    content: str | None = None


class CheckResult(BaseModel):
    """One deterministic check's outcome, informing the verdict (D-077)."""

    command: str
    passed: bool


class CompletionSubmission(BaseModel):
    """A node-step's completion — judgement choice + checks + artifacts + epoch."""

    choice: str  # the `<Choice>{name}</Choice>` the worker emitted (D-042)
    epoch: int  # the executing lease's fence, checked against the chunk's latest (D-007)
    runner_id: str
    from_node_id: str
    check_results: list[CheckResult] = []
    artifacts: list[SubmittedArtifact] = []
    # Set only on a gate-resolving transition (D-045): the decision this transition
    # resolves. Its presence is what makes a transition out of a human-judged node
    # legal — a worker transition there (no decision_id) is rejected (human signoff).
    decision_id: str | None = None
