"""Decision routes — the anonymous **operator** surface (issue #87, #104).

``GET /decisions`` lists the open decisions; ``POST /decisions/{id}/resolutions``
records a person's choice first-write-wins. The controller stays read-only over the
store (``bzh:controller-read-only``), and a runner's bearer token is rejected here
rather than read as anonymous-plus-credential."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import FLEET_VIEW, GATE_RESOLVE
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, resolved_username
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import DecisionRow, DocketEntry
from blizzard.wire.completion import CreateWorkItemProposal, UpdateWorkItemProposal
from blizzard.wire.decision import (
    DecisionChoiceModel,
    DecisionResolutionConflict,
    DecisionResolutionRequest,
    DecisionResolutionResponse,
    DecisionView,
    DocketEntryView,
    OpenDecisionsResponse,
)

router = APIRouter(prefix="/api", tags=["decisions"], dependencies=[Depends(reject_runner_principal)])


def _docket_entry_view(entry: DocketEntry) -> DocketEntryView:
    """Map one :class:`DocketEntry` to its wire view — a malformed stored payload
    renders bare rather than failing the whole gate read (D5)."""
    proposal = entry.proposal
    payload: CreateWorkItemProposal | UpdateWorkItemProposal | None = None
    malformed = False
    try:
        if proposal.kind == "create":
            payload = CreateWorkItemProposal.model_validate_json(proposal.data)
        elif proposal.kind == "update":
            payload = UpdateWorkItemProposal.model_validate_json(proposal.data)
        else:
            malformed = True
    except ValueError:
        malformed = True
    return DocketEntryView(
        proposal_id=proposal.proposal_id,
        node_name=proposal.node_name,
        kind=proposal.kind,
        payload=payload,
        malformed=malformed,
        struck=entry.struck,
        struck_by=entry.struck_by,
        struck_at=iso_utc(entry.struck_at) if entry.struck_at is not None else None,
    )


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
        docket=[_docket_entry_view(e) for e in row.docket],
    )


@router.get("/decisions", response_model=OpenDecisionsResponse, dependencies=[Depends(require(FLEET_VIEW))])
def list_decisions(services: Annotated[HubServices, Depends(get_services)]) -> OpenDecisionsResponse:
    """The fleet's open (unresolved) decisions — gate surfacing."""
    return OpenDecisionsResponse(decisions=[to_decision_view(d) for d in services.chunks.list_open_decisions()])


@router.post(
    "/decisions/{decision_id}/resolutions",
    response_model=DecisionResolutionResponse,
    dependencies=[Depends(require(GATE_RESOLVE))],
)
def resolve_decision(
    decision_id: str,
    request: DecisionResolutionRequest,
    http_request: Request,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Resolve an open decision, first-write-wins CAS.

    ``resolved_by`` is taken from the authenticated session identity, never the request
    body's ``resolved_by`` field — a spoofed value there is silently ignored (issue #91)."""
    pre_decision = services.chunks.get_decision(decision_id)
    change = chunk_events.ChunkChanged.before(services, pre_decision.chunk_id) if pre_decision is not None else None
    try:
        result = services.decisions.resolve(
            decision_id, choice=request.choice, resolved_by=resolved_username(http_request), struck=request.struck
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown decision {decision_id}")
    decision = services.chunks.get_decision(decision_id)
    if not result.resolved:
        conflict = DecisionResolutionConflict(decision_id=decision_id, already_resolved_by=result.resolved_by)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    if decision is not None:
        key = f"decision_resolutions:{decision_id}"
        services.events.publish_decision_resolved(decision.chunk_id, decision_id, key=key)
        # Hardcoded literal, not a derivation — a resolution always lands the chunk
        # back at `running` (see `chunk_events.ChunkChanged.publish`'s docstring).
        chunk_events.ChunkChanged.of(
            services, decision.chunk_id, prev_status=change.prev_status if change is not None else None
        ).publish(cause="decision-resolved", status="running", key=key)
    assert result.resolved and result.resolved_by
    return DecisionResolutionResponse(
        decision_id=decision_id,
        choice=result.choice,
        resolved_by=result.resolved_by,
        resolved_at=(iso_utc(decision.resolved_at) if decision is not None and decision.resolved_at else ""),
    )
