"""Gate-decision wire bodies — the human-loop surface.

A **Decision** is a gate's durable parking row: a multiple-choice ask whose resolution moves
the chunk. A configured gate writes one with a :class:`DecisionSubmission`, in place of a
transition; a graph gate needs none. Resolution — a person picking one — is first-write-wins."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.completion import CreateWorkItemProposal, SubmittedArtifact, UpdateWorkItemProposal, WorkItemProposal


class DecisionChoiceModel(BaseModel):
    """One selectable gate outcome."""

    name: str
    description: str


class DocketEntryView(BaseModel):
    """One of a chunk's not-yet-materialized proposals, as it stands at a gate — the
    proposing node, its kind-shaped ``payload``, and whether an operator has struck it.
    Under ``malformed`` no field but ``proposal_id``, ``node_name``, and ``kind`` may be
    relied on: a stored proposal this hub version can no longer parse renders bare
    rather than failing the whole gate read. ``struck_by``/``struck_at`` are set only
    when ``struck`` is true."""

    proposal_id: str
    node_name: str
    kind: str
    payload: CreateWorkItemProposal | UpdateWorkItemProposal | None = None
    malformed: bool = False
    struck: bool = False
    struck_by: str | None = None
    struck_at: str | None = None


class DecisionSubmission(BaseModel):
    """A runner-config gate: submit a decision in place of a transition, carrying the
    gated step's artifacts, proposed work items, and fencing epoch as one atomic write.
    ``proposals`` is legal only from a node declaring ``proposes_work_items`` (D4, D6)."""

    from_node_id: str  # the gated node — its choices become the decision's
    epoch: int  # the step's lease fence, checked against the chunk's latest
    runner_id: str
    artifacts: list[SubmittedArtifact] = []
    proposals: list[WorkItemProposal] = []
    # The route capability token stamped at enqueue (issue #84a) — see
    # `wire.completion.CompletionSubmission.route_token`; present-only in this phase.
    route_token: str | None = None


class DecisionView(BaseModel):
    """A gate decision in full.

    ``resolved_choice`` is set once a person has decided; ``transitioned`` is true once the
    resolving transition has been recorded. ``docket`` is the *chunk's* pending proposals
    (blizzard#367), not just this decision's own — every gate on the same chunk shares one
    strike record."""

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
    docket: list[DocketEntryView] = []


class OpenDecisionsResponse(BaseModel):
    """The fleet's open (unresolved) decisions — ``blizzard hub decisions``."""

    decisions: list[DecisionView] = []


class DecisionResolutionRequest(BaseModel):
    """A person's choice for an open decision — first-write-wins CAS. ``struck`` names
    the chunk's proposal ids to refuse (blizzard#367); omitted, it passes every proposal."""

    choice: str
    resolved_by: str = "operator"
    struck: list[str] = []


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
