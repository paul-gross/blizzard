"""Decision routes — gate surfacing and resolution (D-045/D-052).

The fleet half of the human gate: ``GET /decisions`` lists the open decisions the
board and ``blizzard hub decisions`` render, and ``POST /decisions/{id}/resolution``
records a person's choice first-write-wins — the same route the board's buttons and
``blizzard hub decide`` hit (D-042/D-045). The controller stays read-only over the
store (``bzh:controller-read-only``): resolution delegates to
:class:`~blizzard.hub.domain.decisions.DecisionService`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import DecisionRow
from blizzard.wire.decision import (
    DecisionChoiceModel,
    DecisionResolutionConflict,
    DecisionResolutionRequest,
    DecisionResolutionResponse,
    DecisionView,
    OpenDecisionsResponse,
)

router = APIRouter(prefix="/api", tags=["decisions"])


def to_decision_view(row: DecisionRow) -> DecisionView:
    """Map a :class:`DecisionRow` to its wire view (shared with the chunk detail)."""
    return DecisionView(
        decision_id=row.decision_id,
        chunk_id=row.chunk_id,
        node_id=row.node_id,
        node_name=row.node_name,
        epoch=row.epoch,
        choices=[DecisionChoiceModel(name=c.name, description=c.description) for c in row.choices],
        submitted_at=row.submitted_at.isoformat(),
        resolved_choice=row.resolved_choice,
        resolved_by=row.resolved_by,
        resolved_at=row.resolved_at.isoformat() if row.resolved_at is not None else None,
        transitioned=row.transitioned,
    )


@router.get("/decisions", response_model=OpenDecisionsResponse)
def list_decisions(services: Annotated[HubServices, Depends(get_services)]) -> OpenDecisionsResponse:
    """The fleet's open (unresolved) decisions — gate surfacing (D-052)."""
    return OpenDecisionsResponse(decisions=[to_decision_view(d) for d in services.chunks.list_open_decisions()])


@router.post("/decisions/{decision_id}/resolution", response_model=DecisionResolutionResponse)
def resolve_decision(
    decision_id: str,
    request: DecisionResolutionRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Resolve an open decision, first-write-wins CAS (D-045)."""
    try:
        result = services.decisions.resolve(decision_id, choice=request.choice, resolved_by=request.resolved_by)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown decision {decision_id}")
    decision = services.chunks.get_decision(decision_id)
    if not result.resolved:
        conflict = DecisionResolutionConflict(decision_id=decision_id, already_resolved_by=result.resolved_by)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    if decision is not None:
        services.events.publish_decision_resolved(decision.chunk_id, decision_id)
        services.events.publish_chunk_changed(decision.chunk_id, "running")
    assert result.resolved and result.resolved_by
    return DecisionResolutionResponse(
        decision_id=decision_id,
        choice=result.choice,
        resolved_by=result.resolved_by,
        resolved_at=(decision.resolved_at.isoformat() if decision is not None and decision.resolved_at else ""),
    )
