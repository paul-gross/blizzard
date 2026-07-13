"""The completion submission (D-027/D-036) — a node-step's atomic, fenced write.

``POST /chunks/{id}/completions`` submits one node-step's completion: the judgement
choice the worker emitted, the check results, and the step's artifacts — **one
atomic, epoch-fenced write** (D-036). Git-commit artifacts name branches already
pushed to the forge (D-026). A stale epoch is rejected and the artifacts never
enter the store (D-007). The reply is the :class:`~blizzard.wire.envelope.ApplyResponse`.

At a human-judged node the hub rejects a transition-style completion (human signoff
required) — the gate counterpart posts a decision instead (P7); the submission
model already carries everything a decision needs, so that route bolts on without
reshaping this contract.
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
