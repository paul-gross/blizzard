"""Flow-submission wire bodies: a chunk's submissions to the flows its hub-node visits land on, each with its
closure."""

from __future__ import annotations

from blizzard.wire.base import WireModel


class FlowSubmissionClosureView(WireModel):
    """How a submission left its flows. ``outcome`` is a verdict's choice or a closure word."""

    outcome: str
    closed_at: str


class FlowSubmissionView(WireModel):
    """One submission of a visit; ``closure`` is ``null`` while it is open."""

    submission_id: str
    node_id: str
    epoch: int
    generation: int
    arrival: int
    submitted_at: str
    closure: FlowSubmissionClosureView | None


class ChunkFlowSubmissionsView(WireModel):
    """``GET /api/chunks/{chunk_id}/flow-submissions`` — every submission of the chunk, in arrival order."""

    chunk_id: str
    items: list[FlowSubmissionView]
