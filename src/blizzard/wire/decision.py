"""Gate-decision wire bodies — the human-loop surface.

A **Decision** is a gate's durable parking row: a multiple-choice ask whose resolution moves
the chunk. A configured gate writes one with a :class:`DecisionSubmission`, in place of a
transition; a graph gate needs none. Resolution — a person picking one — is first-write-wins."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.completion import SubmittedArtifact


class DecisionChoiceModel(BaseModel):
    """One selectable gate outcome."""

    name: str
    description: str


class DecisionSubmission(BaseModel):
    """A runner-config gate: submit a decision in place of a transition.

    Carries the gated step's artifacts and its fencing epoch as one atomic write; the
    node's choice set is resolved from the pinned graph, not sent here."""

    from_node_id: str  # the gated node — its choices become the decision's
    epoch: int  # the step's lease fence, checked against the chunk's latest
    runner_id: str
    artifacts: list[SubmittedArtifact] = []
    # The route capability token stamped at enqueue (issue #84a) — see
    # `wire.completion.CompletionSubmission.route_token`; present-only in this phase.
    route_token: str | None = None


class DecisionView(BaseModel):
    """A gate decision in full.

    ``resolved_choice`` is set once a person has decided; ``transitioned`` is true once the
    resolving transition has been recorded."""

    decision_id: str
    chunk_id: str
    node_id: str
    node_name: str
    epoch: int
    choices: list[DecisionChoiceModel] = []
    submitted_at: str
    resolved_choice: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None
    transitioned: bool = False


class OpenDecisionsResponse(BaseModel):
    """The fleet's open (unresolved) decisions — ``blizzard hub decisions``."""

    decisions: list[DecisionView] = []


class DecisionResolutionRequest(BaseModel):
    """A person's choice for an open decision — first-write-wins CAS."""

    choice: str
    resolved_by: str = "operator"


class DecisionResolutionResponse(BaseModel):
    """The winning resolution — the choice, who, and when."""

    decision_id: str
    choice: str
    resolved_by: str
    resolved_at: str


class DecisionResolutionConflict(BaseModel):
    """The 409 body: the decision was already resolved (the loser is told who won)."""

    decision_id: str
    already_resolved_by: str
    detail: str = "decision already resolved"
