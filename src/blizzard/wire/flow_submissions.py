"""Flow-submission wire bodies: a chunk's submissions to the flows its hub-node visits land on, each with its
closure."""

from __future__ import annotations

from blizzard.wire.base import WireModel


class FlowSubmissionClosureView(WireModel):
    """How a submission left its flows. ``outcome`` is a verdict's choice or a closure word."""

    outcome: str
    closed_at: str


class CapturedLandingTermsView(WireModel):
    """Repository terms captured by the submission's first turn."""

    repository_id: str
    landing_policy: str
    park_timeout_hours: int | None
    landing_position: float | None


class FlowSubmissionView(WireModel):
    """One submission of a visit; ``closure`` is ``null`` while it is open."""

    submission_id: str
    node_id: str
    epoch: int
    generation: int
    arrival: int
    submitted_at: str
    closure: FlowSubmissionClosureView | None
    flow_ids: list[str]
    terms: list[CapturedLandingTermsView]


class ChunkFlowSubmissionsView(WireModel):
    """``GET /api/chunks/{chunk_id}/flow-submissions`` — every submission of the chunk, in arrival order."""

    chunk_id: str
    items: list[FlowSubmissionView]


class SubmissionTurnView(WireModel):
    """A turn, open while its end and outcome are null."""

    turn_id: str
    started_at: str
    ended_at: str | None
    outcome: str | None


class SubmissionTurnsPage(WireModel):
    """A submission's turns in descending turn ID order."""

    items: list[SubmissionTurnView]
    next_before: str | None
