"""The completion submission — a node-step's atomic, fenced write.

``POST /chunks/{id}/completions`` submits one node-step's completion: the judgement
choice, the check results, the step's artifacts, and its proposed work items — **one
atomic, epoch-fenced write**. A stale epoch is rejected before either enters the store.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.work_items import WorkItemPriority


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


class CreateWorkItemProposal(BaseModel):
    """A proposed new work item — a title, a markdown body, and a stated priority."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["create"] = "create"
    title: str
    body: str
    stated_priority: WorkItemPriority = WorkItemPriority.NORMAL


class UpdateWorkItemProposal(BaseModel):
    """A proposed update to an existing work item — its ``{source, ref}`` pointer plus
    evidence to append. Unresolvable at apply time (a closed, withdrawn, or nonexistent
    item) is recorded, not refused — resolving the pointer is left to materialization."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["update"] = "update"
    source: str
    ref: str
    evidence: str


WorkItemProposal = Annotated[CreateWorkItemProposal | UpdateWorkItemProposal, Field(discriminator="kind")]


class CheckResult(BaseModel):
    """One deterministic check's **runner-executed** outcome.

    Carries only ``(command, passed)``; ``output_tail`` deliberately does not ride the wire."""

    command: str
    passed: bool


class CompletionSubmission(BaseModel):
    """A node-step's completion — judgement choice + checks + artifacts + epoch."""

    choice: str  # the `<Choice>{name}</Choice>` the worker emitted
    epoch: int  # the executing lease's fence, checked against the chunk's latest
    runner_id: str
    from_node_id: str
    # Runner-executed checks; empty when no checks are declared.
    check_results: list[CheckResult] = []
    artifacts: list[SubmittedArtifact] = []
    # Legal only from nodes declaring `proposes_work_items`.
    proposals: list[WorkItemProposal] = []
    # Required to transition out of a human-judged gate.
    decision_id: str | None = None
    # Enqueue-time route token; optional, not hub-enforced.
    route_token: str | None = None
    # Optional owning-lease check; absent it, match the runner alone.
    lease_id: str | None = None
