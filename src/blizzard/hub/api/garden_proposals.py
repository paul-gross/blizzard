"""Garden-proposal routes — the read routes render a proposal's closure
once one exists; the two closing writes are their own POST routes, both
human-plane and gated on `Permission.CHUNK_CONTROL` — the same permission a not-chunk-scoped
work-item write already carries."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.chunk.ingest import IngestConflict
from blizzard.hub.domain.garden.findings.model import Finding
from blizzard.hub.domain.garden.proposals.closure import (
    GardenProposalBodyWithoutMint,
    GardenProposalClosure,
    GardenProposalPassReasonRequired,
)
from blizzard.hub.domain.garden.proposals.model import (
    DuplicateProposalFindingError,
    GardenProposal,
    GardenProposalAlreadyClosed,
    GardenProposalBlankFieldError,
    GardenProposalEdit,
    GardenProposalEmptyEditError,
    GardenProposalFindingAlreadyLinkedError,
    GardenProposalFindingExitedError,
    GardenProposalFindingNotLinkedError,
    GardenProposalNoFindingsError,
    GardenProposalVerb,
)
from blizzard.hub.domain.garden.proposals.resolution import resolve_proposal_findings
from blizzard.hub.domain.graph.authoring import DefaultGraphRetired
from blizzard.hub.domain.kernel.pagination import DEFAULT_LIMIT, MAX_LIMIT, MalformedCursor
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.hub.domain.work_items.model import WorkItemFieldBlank
from blizzard.wire.chunk import ChunkIngestConflict
from blizzard.wire.garden_proposal import (
    GardenProposalAcceptRequest,
    GardenProposalAcceptResponse,
    GardenProposalClosureView,
    GardenProposalCreateRequest,
    GardenProposalEditRequest,
    GardenProposalFindingsRequest,
    GardenProposalPassRequest,
    GardenProposalsPageView,
    GardenProposalView,
)

router = APIRouter(prefix="/api", tags=["garden-proposals"], dependencies=[Depends(reject_runner_principal)])


def closure_view(closure: GardenProposalClosure) -> GardenProposalClosureView:
    return GardenProposalClosureView(
        closure=closure.closure,
        reason=closure.reason,
        closed_by=closure.closed_by,
        closed_at=iso_utc(closure.closed_at),
        item_outcome=closure.item_outcome,
        source=closure.source,
        ref=closure.ref,
    )


def garden_proposal_view(proposal: GardenProposal, closure: GardenProposalClosure | None) -> GardenProposalView:
    """The one ``GardenProposal`` -> ``GardenProposalView`` projection — the
    ``finding_view`` shape."""
    # `class_`'s alias is the Python keyword `class` — constructed by alias via
    # `model_validate`, the `finding_view` shape.
    return GardenProposalView.model_validate(
        {
            "proposal_id": proposal.proposal_id,
            "origin": proposal.origin,
            "routine_name": proposal.routine_name,
            "created_by": proposal.created_by,
            "class": proposal.class_,
            "title": proposal.title,
            "body": proposal.body,
            "findings": list(proposal.findings),
            "created_at": iso_utc(proposal.created_at),
            "closure": closure_view(closure) if closure is not None else None,
        }
    )


def _get_or_404(proposal_id: str, services: HubServices) -> GardenProposal:
    proposal = services.garden_proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown garden proposal {proposal_id}")
    return proposal


def _require_open_for(verb: GardenProposalVerb, proposal: GardenProposal, services: HubServices) -> None:
    """409 when `verb` is refused as closed — checked before any finding id is resolved,
    since a closed proposal refuses every verb as closed ahead of any other refusal."""
    try:
        proposal.require_legal(verb, services.garden_proposal_closures.get(proposal.proposal_id))
    except GardenProposalAlreadyClosed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _resolve_findings_or_422(finding_ids: list[str], services: HubServices) -> list[Finding]:
    """`IReadFindingRepository.get_many` resolved against `finding_ids`, preserving the
    caller's order; 422s naming every id `get_many` dropped — the whole
    call is refused, not the unknown ones alone."""
    found = services.findings.get_many(finding_ids)
    missing = sorted({fid for fid in finding_ids if fid not in found})
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown finding id(s): {', '.join(missing)}",
        )
    return [found[fid] for fid in finding_ids]


@router.get(
    "/garden-proposals", response_model=GardenProposalsPageView, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
def list_garden_proposals(
    services: Annotated[HubServices, Depends(get_services)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    origin: Annotated[GardenProposalOrigin | None, Query()] = None,
) -> GardenProposalsPageView:
    """Every garden proposal, newest first, bounded and keyset-paginated.
    `origin` narrows to `routine-run` or `operator` proposals."""
    try:
        page = services.garden_proposals.list_page(cursor=cursor, limit=limit, origin=origin)
    except MalformedCursor as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="malformed cursor") from exc
    closures = services.garden_proposal_closures.get_many([p.proposal_id for p in page.proposals])
    return GardenProposalsPageView(
        proposals=[garden_proposal_view(p, closures.get(p.proposal_id)) for p in page.proposals],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/garden-proposals/{proposal_id}",
    response_model=GardenProposalView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def get_garden_proposal(
    proposal_id: str, services: Annotated[HubServices, Depends(get_services)]
) -> GardenProposalView:
    """One garden proposal's whole record; 404 on an unknown id."""
    proposal = _get_or_404(proposal_id, services)
    return garden_proposal_view(proposal, services.garden_proposal_closures.get(proposal_id))


@router.post("/garden-proposals/{proposal_id}/pass", response_model=GardenProposalView)
def pass_garden_proposal(
    proposal_id: str,
    request: GardenProposalPassRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
) -> GardenProposalView:
    """Pass the proposal at `{proposal_id}`, recording the given reason. Passing is not a
    dismissal — it is the note that stops a later run raising the same response as
    though it were new. 404 for an unknown proposal, 409 when the proposal already
    carries a closure — closure is terminal, so it wins over every other refusal — and
    422 for a blank reason."""
    proposal = _get_or_404(proposal_id, services)
    try:
        closure = services.garden_proposal_closure.pass_(proposal, reason=request.reason, by=identity.user_id)
    except GardenProposalPassReasonRequired as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except GardenProposalAlreadyClosed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return garden_proposal_view(proposal, closure)


@router.post("/garden-proposals/{proposal_id}/accept", response_model=GardenProposalAcceptResponse)
def accept_garden_proposal(
    proposal_id: str,
    request: GardenProposalAcceptRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
) -> object:
    """Accept the proposal at `{proposal_id}`. When `mint_work_item` is true, mints a linked
    hub work item from `body` (or the proposal's own), wrapped in the "Related findings"
    template when the proposal names findings and bare when it names none. When it is
    false, mints nothing and records the decline. Promotes nothing and changes no
    finding's state. A blank reason is stored as none. 404 unknown proposal, 422 a `body`
    with `mint_work_item` false or a minted item left with a blank title or body, 409
    already closed or a raced ingest, 503 the packaged default graph retired."""
    proposal = _get_or_404(proposal_id, services)
    proposal_findings = resolve_proposal_findings(services.findings, proposal.findings)
    try:
        accepted = services.garden_proposal_closure.accept(
            proposal,
            reason=request.reason,
            by=identity.user_id,
            body=request.body,
            mint=request.mint_work_item,
            findings=proposal_findings,
        )
    except (GardenProposalBodyWithoutMint, WorkItemFieldBlank) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except GardenProposalAlreadyClosed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DefaultGraphRetired as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
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
        **garden_proposal_view(proposal, accepted.closure).model_dump(), chunk_id=accepted.chunk_id
    )


@router.post(
    "/garden-proposals",
    response_model=GardenProposalView,
    status_code=status.HTTP_201_CREATED,
)
def create_garden_proposal(
    request: GardenProposalCreateRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
) -> GardenProposalView:
    """Mint an operator-authored proposal, naming `routine` when the
    caller names one, else none. 422 for a blank title/class/body, an unknown routine,
    or an unknown, exited, or duplicate finding id — the whole call is refused, nothing
    is linked."""
    routine = None
    if request.routine is not None:
        routine = services.routines.get_by_name(request.routine)
        if routine is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"unknown routine {request.routine!r}"
            )
    findings = _resolve_findings_or_422(request.findings, services)
    try:
        proposal = services.garden_proposal_authoring.create_operator(
            created_by=identity.user_id,
            routine=routine,
            class_=request.class_,
            title=request.title,
            body=request.body,
            findings=findings,
        )
    except (DuplicateProposalFindingError, GardenProposalFindingExitedError, GardenProposalBlankFieldError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return garden_proposal_view(proposal, None)


@router.patch(
    "/garden-proposals/{proposal_id}",
    response_model=GardenProposalView,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def edit_garden_proposal(
    proposal_id: str, request: GardenProposalEditRequest, services: Annotated[HubServices, Depends(get_services)]
) -> GardenProposalView:
    """Replace the given fields of `{proposal_id}` in place, all-or-nothing
    — works on either origin while open. 404 unknown proposal, 409 already closed,
    422 a blank title/class/body or an edit naming no field."""
    proposal = _get_or_404(proposal_id, services)
    edit = GardenProposalEdit(
        title=request.title if request.title is not None else UNSET,
        class_=request.class_ if request.class_ is not None else UNSET,
        body=request.body if request.body is not None else UNSET,
    )
    try:
        updated = services.garden_proposal_authoring.edit(proposal, edit)
    except (GardenProposalBlankFieldError, GardenProposalEmptyEditError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except GardenProposalAlreadyClosed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return garden_proposal_view(updated, services.garden_proposal_closures.get(proposal_id))


@router.post(
    "/garden-proposals/{proposal_id}/attach",
    response_model=GardenProposalView,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def attach_garden_proposal_findings(
    proposal_id: str,
    request: GardenProposalFindingsRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> GardenProposalView:
    """Link the given finding ids to `{proposal_id}` — works on either origin
    while open. 404 unknown proposal, 409 already closed, 422 no finding id, an unknown,
    exited, or duplicate finding id, or one already linked to this proposal — the whole
    call is refused, nothing is linked."""
    proposal = _get_or_404(proposal_id, services)
    _require_open_for(GardenProposalVerb.ATTACH, proposal, services)
    findings = _resolve_findings_or_422(request.findings, services)
    try:
        updated = services.garden_proposal_authoring.attach(proposal, findings)
    except (
        GardenProposalNoFindingsError,
        DuplicateProposalFindingError,
        GardenProposalFindingExitedError,
        GardenProposalFindingAlreadyLinkedError,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except GardenProposalAlreadyClosed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return garden_proposal_view(updated, services.garden_proposal_closures.get(proposal_id))


@router.post(
    "/garden-proposals/{proposal_id}/detach",
    response_model=GardenProposalView,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def detach_garden_proposal_findings(
    proposal_id: str,
    request: GardenProposalFindingsRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> GardenProposalView:
    """Unlink the given finding ids from `{proposal_id}` — works on either
    origin while open. 404 unknown proposal, 409 already closed, 422 no finding id, an
    unknown or duplicate id, or one not linked to this proposal."""
    proposal = _get_or_404(proposal_id, services)
    _require_open_for(GardenProposalVerb.DETACH, proposal, services)
    findings = _resolve_findings_or_422(request.findings, services)
    try:
        updated = services.garden_proposal_authoring.detach(proposal, findings)
    except (GardenProposalNoFindingsError, DuplicateProposalFindingError, GardenProposalFindingNotLinkedError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except GardenProposalAlreadyClosed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return garden_proposal_view(updated, services.garden_proposal_closures.get(proposal_id))
