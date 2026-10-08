"""The completion submission — a node-step's atomic, fenced write.

``POST /chunks/{id}/completions`` submits one node-step's completion: the judgement
choice, the check results, and the step's artifacts — **one atomic, epoch-fenced
write**. A stale epoch is rejected before either enters the store.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.foundation.artifacts import ArtifactKind


class SubmittedArtifact(BaseModel):
    """An artifact committed atomically with the completion."""

    name: str
    kind: ArtifactKind
    # Pushed git_commit; forge is the worker's declared origin.
    forge: str | None = None
    repo: str | None = None
    branch_name: str | None = None
    commit_hash: str | None = None
    # asset variant
    content: str | None = None
    # Explicit attach, rather than the judgement fallback.
    attached: bool = False


class CheckResult(BaseModel):
    """One deterministic check's **runner-executed** outcome.

    Carries only ``(command, passed)``; ``output_tail`` deliberately does not ride the wire."""

    command: str
    passed: bool


class CompletionSubmission(BaseModel):
    """A node-step's completion — judgement choice + checks + artifacts + epoch."""

    choice: str  # the `<Choice>{name}</Choice>` the worker emitted
    epoch: int  # the executing lease's fence, checked against the chunk's latest
    from_node_id: str
    # Runner-executed checks; empty when no checks are declared.
    check_results: list[CheckResult] = []
    artifacts: list[SubmittedArtifact] = []
    # Required to transition out of a human-judged gate.
    decision_id: str | None = None
    # Enqueue-time route token; optional.
    route_token: str | None = None
    # Optional owning-lease check; absent it, match the runner alone.
    lease_id: str | None = None
