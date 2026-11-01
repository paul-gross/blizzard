"""Flow-submission routes — a chunk's submissions to its flows, with their closures. Gated on
:attr:`~blizzard.auth_core.Permission.FLEET_VIEW`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.domain.delivery.flows import SubmissionRecord
from blizzard.wire.flow_submissions import (
    CapturedLandingTermsView,
    ChunkFlowSubmissionsView,
    FlowSubmissionClosureView,
    FlowSubmissionView,
    SubmissionTurnsPage,
    SubmissionTurnView,
)

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
        flow_ids=list(record.flow_ids),
        terms=[
            CapturedLandingTermsView(
                repository_id=t.repository_id,
                landing_policy=t.landing_policy,
                park_timeout_hours=t.park_timeout_hours,
                landing_position=t.landing_position,
            )
            for t in record.terms
        ],
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


@router.get(
    "/chunks/{chunk_id}/flow-submissions/{submission_id}/turns",
    response_model=SubmissionTurnsPage,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def list_submission_turns(
    chunk_id: str,
    submission_id: str,
    services: Services,
    before: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SubmissionTurnsPage:
    """Turns of one submission, newest ID first; includes turns holding no flows."""
    if not services.flows.submission_belongs_to(submission_id, chunk_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown submission")
    rows = services.flows.turns_page(submission_id, before=before, limit=limit + 1)
    page = rows[:limit]
    return SubmissionTurnsPage(
        items=[
            SubmissionTurnView(
                turn_id=turn.turn_id,
                started_at=iso_utc(turn.started_at),
                ended_at=iso_utc(turn.ended_at) if turn.ended_at is not None else None,
                outcome=turn.outcome,
            )
            for turn in page
        ],
        next_before=page[-1].turn_id if len(rows) > limit else None,
    )
