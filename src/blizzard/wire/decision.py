"""Gate-decision wire bodies — the human-loop surface.

A **Decision** is a gate's durable parking row: a multiple-choice ask whose
resolution moves the chunk. Two shapes write one:

* the **runner-config gate** submits a :class:`DecisionSubmission` to
  ``POST /chunks/{id}/decisions`` — a runner choosing a decision in place of a
  transition for a node it was configured to gate. The choice set is the
  node's own (the hub is the single source of truth for the graph), so the runner
  sends only the step's artifacts and its fence.
* a **graph gate** needs no submission — the hub opens the decision itself when a
  transition lands on a human-judged node (see :mod:`blizzard.hub.domain.apply`).

Resolution — a person picking one choice — is first-write-wins at
``POST /decisions/{id}/resolutions`` (:class:`DecisionResolutionRequest`), exactly
like an answer. The holding runner picks the resolution up on PULL and records the
resolving transition referencing ``decision_id``.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.completion import SubmittedArtifact


class DecisionChoiceModel(BaseModel):
    """One selectable gate outcome — a button on the board/bot."""

    name: str
    description: str


class DecisionSubmission(BaseModel):
    """A runner-config gate: submit a decision in place of a transition.

    Carries the gated step's artifacts and its fencing epoch — one atomic, epoch-fenced
    write, exactly where a worker-judged node would have submitted its transition. The
    node's choice set is supplied by the hub from the pinned graph, not sent here.
    """

    from_node_id: str  # the gated node — its choices become the decision's
    epoch: int  # the step's lease fence, checked against the chunk's latest
    runner_id: str
    artifacts: list[SubmittedArtifact] = []
    # The route capability token stamped at enqueue (issue #84a) — see
    # `wire.completion.CompletionSubmission.route_token`; present-only in this phase.
    route_token: str | None = None


class DecisionView(BaseModel):
    """A gate decision in full — the board's card and the runner's pickup.

    ``resolved_choice`` is set once a person has decided; ``transitioned`` is true once
    the holding runner has recorded the resolving transition. The runner acts on a
    decision that is resolved but not yet transitioned.
    """

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
