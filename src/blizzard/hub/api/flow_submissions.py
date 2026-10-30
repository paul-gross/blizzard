"""Flow-submission routes — a chunk's submissions to its flows, with their closures. Gated on
:attr:`~blizzard.auth_core.Permission.FLEET_VIEW`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.domain.delivery.flows import SubmissionRecord
from blizzard.wire.flow_submissions import ChunkFlowSubmissionsView, FlowSubmissionClosureView, FlowSubmissionView

router = APIRouter(
    prefix="/api", tags=["flows"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)


def _view(record: SubmissionRecord) -> FlowSubmissionView:
    closure = record.closure
    return FlowSubmissionView(
        submission_id=record.submission_id,
        node_id=record.node_id,
        epoch=record.epoch,
        generation=record.generation,
        arrival=record.arrival,
        submitted_at=iso_utc(record.submitted_at),
        closure=None
        if closure is None
        else FlowSubmissionClosureView(outcome=closure.outcome, closed_at=iso_utc(closure.closed_at)),
    )


@router.get(
    "/chunks/{chunk_id}/flow-submissions",
    response_model=ChunkFlowSubmissionsView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def list_flow_submissions(chunk_id: str, services: Services) -> ChunkFlowSubmissionsView:
    """Every submission of the chunk's visits, open or closed, in arrival order. Unpaged: the read is bounded by one
    chunk's visits — a submission per hub-node visit generation."""
    if services.chunks.record.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    return ChunkFlowSubmissionsView(
        chunk_id=chunk_id, items=[_view(record) for record in services.flows.submissions_of(chunk_id)]
    )
