"""Garden-proposal routes (blizzard#390) — the read routes render a proposal's closure
once one exists; the two closing writes are blizzard#395's own POST routes, both
human-plane and gated on `CHUNK_CONTROL` — the same permission a not-chunk-scoped
work-item write already carries."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import CHUNK_CONTROL, FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.garden_proposal_closure import (
    GardenProposalAlreadyClosed,
    GardenProposalClosure,
    GardenProposalPassReasonRequired,
)
from blizzard.hub.domain.garden_proposals import GardenProposal
from blizzard.hub.domain.graph_authoring import DefaultGraphRetired
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.wire.chunk import ChunkIngestConflict
from blizzard.wire.garden_proposal import (
    GardenProposalAcceptRequest,
    GardenProposalAcceptResponse,
    GardenProposalClosureView,
    GardenProposalPassRequest,
    GardenProposalView,
)

router = APIRouter(prefix="/api", tags=["garden-proposals"], dependencies=[Depends(reject_runner_principal)])


def _closure_view(closure: GardenProposalClosure) -> GardenProposalClosureView:
    return GardenProposalClosureView(
        closure=closure.closure,
        reason=closure.reason,
        closed_by=closure.closed_by,
        closed_at=iso_utc(closure.closed_at),
        item_outcome=closure.item_outcome,
        source=closure.source,
        ref=closure.ref,
    )


def _proposal_view(proposal: GardenProposal, closure: GardenProposalClosure | None) -> GardenProposalView:
    # `class_`'s alias is the Python keyword `class` — constructed by alias via
    # `model_validate`, the `finding_view` shape.
    return GardenProposalView.model_validate(
        {
            "proposal_id": proposal.proposal_id,
            "routine_name": proposal.routine_name,
            "class": proposal.class_,
            "title": proposal.title,
            "body": proposal.body,
            "findings": list(proposal.findings),
            "created_at": iso_utc(proposal.created_at),
            "closure": _closure_view(closure) if closure is not None else None,
        }
    )


def _get_or_404(proposal_id: str, services: HubServices) -> GardenProposal:
    proposal = services.garden_proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown garden proposal {proposal_id}")
    return proposal


@router.get("/garden-proposals", response_model=list[GardenProposalView], dependencies=[Depends(require(FLEET_VIEW))])
def list_garden_proposals(services: Annotated[HubServices, Depends(get_services)]) -> list[GardenProposalView]:
    """Every garden proposal, newest first."""
    proposals = services.garden_proposals.list_all()
    closures = services.garden_proposal_closures.get_many([p.proposal_id for p in proposals])
    return [_proposal_view(p, closures.get(p.proposal_id)) for p in proposals]


@router.get(
    "/garden-proposals/{proposal_id}",
    response_model=GardenProposalView,
    dependencies=[Depends(require(FLEET_VIEW))],
)
def get_garden_proposal(
    proposal_id: str, services: Annotated[HubServices, Depends(get_services)]
) -> GardenProposalView:
    """One garden proposal's whole record; 404 on an unknown id."""
    proposal = _get_or_404(proposal_id, services)
    return _proposal_view(proposal, services.garden_proposal_closures.get(proposal_id))


@router.post("/garden-proposals/{proposal_id}/pass", response_model=GardenProposalView)
def pass_garden_proposal(
    proposal_id: str,
    request: GardenProposalPassRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> GardenProposalView:
    """Pass the proposal at PROPOSAL_ID, recording the given reason. Passing is not a
    dismissal — it is the note that stops a later run raising the same response as
    though it were new. 404 for an unknown proposal, 422 for a blank reason, 409 when
    the proposal already carries a closure — closure is terminal."""
    proposal = _get_or_404(proposal_id, services)
    try:
        closure = services.garden_proposal_closure.pass_(proposal, reason=request.reason, by=identity.user_id)
    except GardenProposalPassReasonRequired as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except GardenProposalAlreadyClosed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _proposal_view(proposal, closure)


@router.post("/garden-proposals/{proposal_id}/accept", response_model=GardenProposalAcceptResponse)
def accept_garden_proposal(
    proposal_id: str,
    request: GardenProposalAcceptRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> object:
    """Accept the proposal at PROPOSAL_ID: mints a linked hub work item by default (the
    proposal's own body unless overridden), or records the decline when
    `mint_work_item` is false — never inferred from an absent link. Promotes nothing and
    changes no finding's state. 404 unknown proposal, 409 already closed or a raced
    ingest, 503 the packaged default graph retired."""
    proposal = _get_or_404(proposal_id, services)
    existing = services.garden_proposal_closures.get(proposal_id)
    if existing is not None:
        already = GardenProposalAlreadyClosed(proposal_id, existing)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(already)) from None
    graph = None
    if request.mint_work_item:
        try:
            graph = services.graph_mint.ensure_default(
                services.default_graph_doc, definition_yaml=services.default_graph_yaml
            )
        except DefaultGraphRetired as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    try:
        accepted = services.garden_proposal_closure.accept(
            proposal,
            reason=request.reason,
            by=identity.user_id,
            body=request.body,
            mint=request.mint_work_item,
            graph=graph,
        )
    except GardenProposalAlreadyClosed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IngestConflict as exc:
        conflict = ChunkIngestConflict(
            existing_chunk_id=exc.existing_chunk_id, source=exc.pointer.source, ref=exc.pointer.ref
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    if accepted.chunk_id is not None:
        # A freshly minted chunk rests `not_ready`, exactly as a `POST
        # /work-sources/hub/items` mint does.
        chunk_events.ChunkChanged.of(services, accepted.chunk_id, prev_status=None).publish(
            cause="minted", key=f"chunks:{accepted.chunk_id}"
        )
        services.events.publish_queue_changed()  # mint adds the chunk to the backlog list
    return GardenProposalAcceptResponse(
        **_proposal_view(proposal, accepted.closure).model_dump(), chunk_id=accepted.chunk_id
    )
