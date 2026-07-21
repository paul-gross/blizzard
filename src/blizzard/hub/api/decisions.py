"""Decision routes — gate surfacing and resolution — the anonymous **operator**
surface (issue #87, #104).

The human-gate half a person drives: ``GET /decisions`` lists the open decisions the
board and ``blizzard hub decisions`` render, and ``POST /decisions/{id}/resolutions``
records a person's choice first-write-wins — the same route the board's buttons and
``blizzard hub decide`` hit. The controller stays read-only over the
store (``bzh:controller-read-only``): resolution delegates to
:class:`~blizzard.hub.domain.decisions.DecisionService`.
``dependencies=[Depends(reject_runner_principal)]`` rejects a runner's bearer token
here rather than treating it as anonymous-plus-credential — a runner's token is
confined to the fleet router.

``POST /decisions/{id}/resolution`` (singular) is kept mounted as a ``deprecated=True``
alias that delegates to the successor handler and marks the response deprecated
(:func:`blizzard.hub.api.deprecation.mark_deprecated`) — behavior unchanged.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.deprecation import mark_deprecated
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

router = APIRouter(prefix="/api", tags=["decisions"], dependencies=[Depends(reject_runner_principal)])


def to_decision_view(row: DecisionRow) -> DecisionView:
    """Map a :class:`DecisionRow` to its wire view (shared with the chunk detail)."""
    return DecisionView(
        decision_id=row.decision_id,
        chunk_id=row.chunk_id,
        node_id=row.node_id,
        node_name=row.node_name,
        epoch=row.epoch,
        choices=[DecisionChoiceModel(name=c.name, description=c.description) for c in row.choices],
        submitted_at=iso_utc(row.submitted_at),
        resolved_choice=row.resolved_choice,
        resolved_by=row.resolved_by,
        resolved_at=iso_utc(row.resolved_at) if row.resolved_at is not None else None,
        transitioned=row.transitioned,
    )


@router.get("/decisions", response_model=OpenDecisionsResponse)
def list_decisions(services: Annotated[HubServices, Depends(get_services)]) -> OpenDecisionsResponse:
    """The fleet's open (unresolved) decisions — gate surfacing."""
    return OpenDecisionsResponse(decisions=[to_decision_view(d) for d in services.chunks.list_open_decisions()])


@router.post("/decisions/{decision_id}/resolutions", response_model=DecisionResolutionResponse)
def resolve_decision(
    decision_id: str,
    request: DecisionResolutionRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Resolve an open decision, first-write-wins CAS."""
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
        resolved_at=(iso_utc(decision.resolved_at) if decision is not None and decision.resolved_at else ""),
    )


@router.post("/decisions/{decision_id}/resolution", response_model=DecisionResolutionResponse, deprecated=True)
def resolve_decision_singular(
    decision_id: str,
    request: DecisionResolutionRequest,
    response: Response,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Deprecated alias of ``POST /api/decisions/{id}/resolutions`` — kept for
    version-skewed clients (``blizzard hub decide``, until it migrates)."""
    result = resolve_decision(decision_id, request, services)
    # The successor's 409-conflict path returns its own JSONResponse — headers set on
    # the injected `response` are then discarded, so mark whichever object is actually
    # returned to the client.
    target = result if isinstance(result, Response) else response
    mark_deprecated(target, successor=f"/api/decisions/{decision_id}/resolutions")
    return result
