"""Garden-proposal routes — the read routes render a proposal's closure
once one exists; the two closing writes are their own POST routes, both
human-plane and gated on `Permission.CHUNK_CONTROL` — the same permission a not-chunk-scoped
work-item write already carries."""

from __future__ import annotations

from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Query, status

from blizzard.auth_core import Permission
from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events, problems, problems_admin, problems_garden
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.chunk_views import ingest_conflict_refusal
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.project_label_fields import ProjectLabelFields
from blizzard.hub.api.project_labels import ProjectLabels
from blizzard.hub.api.project_params import PathProject, QueryProject, project_of_call
from blizzard.hub.api.urgencies import stated_urgency, urgency_refusal
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.domain.chunk.ingest import IngestConflict
from blizzard.hub.domain.garden.findings.model import Finding, IReadFindingRepository
from blizzard.hub.domain.garden.proposals.closure import (
    GardenProposalClosure,
    GardenProposalClosureService,
    GardenProposalMintFieldWithoutMint,
    GardenProposalPassReasonRequired,
    IReadGardenProposalClosureRepository,
)
from blizzard.hub.domain.garden.proposals.model import (
    DuplicateProposalFindingError,
    GardenProposal,
    GardenProposalAlreadyClosed,
    GardenProposalAuthoring,
    GardenProposalBlankFieldError,
    GardenProposalEdit,
    GardenProposalEmptyEditError,
    GardenProposalFindingAlreadyLinkedError,
    GardenProposalFindingExitedError,
    GardenProposalFindingNotLinkedError,
    GardenProposalFindingOtherProjectError,
    GardenProposalNoFindingsError,
    GardenProposalRoutineOtherProjectError,
    GardenProposalVerb,
    IReadGardenProposalRepository,
)
from blizzard.hub.domain.garden.proposals.resolution import resolve_proposal_findings
from blizzard.hub.domain.garden.routines import IReadRoutineRepository
from blizzard.hub.domain.graph.authoring import DefaultGraphRetired
from blizzard.hub.domain.kernel.pagination import DEFAULT_LIMIT, MAX_LIMIT, MalformedCursor
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.hub.domain.projects.model import IReadProjectRepository, Project
from blizzard.hub.domain.urgency.model import UrgencyRetired, UrgencyUnknown
from blizzard.hub.domain.urgency.services import UrgencyReading
from blizzard.hub.domain.work_items.model import WorkItemFieldBlank
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.work_sources.source import IWorkSourceRegistry
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

_GUARDS = [Depends(reject_runner_principal), Depends(tenant_bound)]
router = APIRouter(prefix="/api", tags=["garden-proposals"], dependencies=_GUARDS)
project_router = APIRouter(prefix="/api/projects/{project}", tags=["garden-proposals"], dependencies=_GUARDS)
Proposals = FromDishka[IReadGardenProposalRepository]
Closures = FromDishka[IReadGardenProposalClosureRepository]
Closing = FromDishka[GardenProposalClosureService]
Authoring = FromDishka[GardenProposalAuthoring]
Findings = FromDishka[IReadFindingRepository]
Routines = FromDishka[IReadRoutineRepository]
ProjectReads = FromDishka[IReadProjectRepository]
Urgencies = FromDishka[UrgencyReading]
Sources = FromDishka[IWorkSourceRegistry]
Frames = FromDishka[ChunkFrameLoader]
Events = FromDishka[EventBroker]


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


def garden_proposal_view(
    proposal: GardenProposal, closure: GardenProposalClosure | None, labels: ProjectLabelFields | None = None
) -> GardenProposalView:
    """The one ``GardenProposal`` -> ``GardenProposalView`` projection, labelled with its project when ``labels``
    is given — the ``finding_view`` shape."""
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
            **(labels if labels is not None else {}),
        }
    )


def _labelled(
    proposal: GardenProposal, closure: GardenProposalClosure | None, projects: IReadProjectRepository
) -> GardenProposalView:
    """``proposal``'s view, labelled from one read of its project."""
    labels = ProjectLabels.read(projects, [proposal.project_id]).fields(proposal.project_id)
    return garden_proposal_view(proposal, closure, labels)


def _get_or_404(proposal_id: str, proposals: IReadGardenProposalRepository) -> GardenProposal:
    proposal = proposals.get(proposal_id)
    if proposal is None:
        raise ProblemHTTPException(
            partial(problems_garden.garden_proposal_not_found_problem, f"unknown garden proposal {proposal_id}")
        )
    return proposal


def _require_open_for(
    verb: GardenProposalVerb, proposal: GardenProposal, closures: IReadGardenProposalClosureRepository
) -> None:
    """409 when `verb` is refused as closed — checked before any finding id is resolved,
    since a closed proposal refuses every verb as closed ahead of any other refusal."""
    try:
        proposal.require_legal(verb, closures.get(proposal.proposal_id))
    except GardenProposalAlreadyClosed as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_already_closed_problem, str(exc))) from exc


def _resolve_findings_or_422(finding_ids: list[str], findings: IReadFindingRepository) -> list[Finding]:
    """`IReadFindingRepository.get_many` resolved against `finding_ids`, preserving the
    caller's order; 422s naming every id `get_many` dropped — the whole
    call is refused, not the unknown ones alone."""
    found = findings.get_many(finding_ids)
    missing = sorted({fid for fid in finding_ids if fid not in found})
    if missing:
        raise ProblemHTTPException(
            partial(problems_garden.garden_proposal_invalid_problem, f"unknown finding id(s): {', '.join(missing)}")
        )
    return [found[fid] for fid in finding_ids]


def _page(
    proposals: IReadGardenProposalRepository,
    closures: IReadGardenProposalClosureRepository,
    projects: IReadProjectRepository,
    project_id: str | None,
    *,
    cursor: str | None,
    limit: int,
    origin: GardenProposalOrigin | None,
) -> GardenProposalsPageView:
    try:
        page = proposals.list_page(cursor=cursor, limit=limit, origin=origin, project_id=project_id)
    except MalformedCursor as exc:
        raise ProblemHTTPException(partial(problems.malformed_cursor_problem, exc)) from exc
    held = closures.get_many([p.proposal_id for p in page.proposals])
    labels = ProjectLabels.read(projects, (p.project_id for p in page.proposals))
    return GardenProposalsPageView(
        proposals=[
            garden_proposal_view(p, held.get(p.proposal_id), labels.fields(p.project_id)) for p in page.proposals
        ],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/garden-proposals", response_model=GardenProposalsPageView, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
@inject_sync
def list_garden_proposals(
    proposals: Proposals,
    closures: Closures,
    projects: ProjectReads,
    project: QueryProject,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    origin: Annotated[GardenProposalOrigin | None, Query()] = None,
) -> GardenProposalsPageView:
    """Every garden proposal, newest first, bounded and keyset-paginated — of the project `?project=` names, else
    of every project. `origin` narrows to `routine-run` or `operator` proposals."""
    return _page(proposals, closures, projects, project, cursor=cursor, limit=limit, origin=origin)


@project_router.get(
    "/garden-proposals", response_model=GardenProposalsPageView, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
@inject_sync
def list_project_garden_proposals(
    proposals: Proposals,
    closures: Closures,
    projects: ProjectReads,
    project: PathProject,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    origin: Annotated[GardenProposalOrigin | None, Query()] = None,
) -> GardenProposalsPageView:
    """The project's garden proposals, newest first, bounded and keyset-paginated; `origin` narrows to one
    origin. 404 on an unknown project."""
    return _page(proposals, closures, projects, project.project_id, cursor=cursor, limit=limit, origin=origin)


@router.get(
    "/garden-proposals/{proposal_id}",
    response_model=GardenProposalView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
@inject_sync
def get_garden_proposal(
    proposal_id: str, proposals: Proposals, closures: Closures, projects: ProjectReads
) -> GardenProposalView:
    """One garden proposal's whole record; 404 on an unknown id."""
    proposal = _get_or_404(proposal_id, proposals)
    return _labelled(proposal, closures.get(proposal_id), projects)


@router.post("/garden-proposals/{proposal_id}/pass", response_model=GardenProposalView)
@inject_sync
def pass_garden_proposal(
    proposal_id: str,
    request: GardenProposalPassRequest,
    proposals: Proposals,
    closing: Closing,
    projects: ProjectReads,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
) -> GardenProposalView:
    """Pass the proposal at `{proposal_id}`, recording the given reason. Passing is not a
    dismissal — it is the note that stops a later run raising the same response as
    though it were new. 404 for an unknown proposal, 409 when the proposal already
    carries a closure — closure is terminal, so it wins over every other refusal — and
    422 for a blank reason."""
    proposal = _get_or_404(proposal_id, proposals)
    try:
        closure = closing.pass_(proposal, reason=request.reason, by=identity.user_id)
    except GardenProposalPassReasonRequired as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_invalid_problem, str(exc))) from exc
    except GardenProposalAlreadyClosed as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_already_closed_problem, str(exc))) from exc
    return _labelled(proposal, closure, projects)


@router.post("/garden-proposals/{proposal_id}/accept", response_model=GardenProposalAcceptResponse)
@inject_sync
def accept_garden_proposal(
    proposal_id: str,
    request: GardenProposalAcceptRequest,
    proposals: Proposals,
    closing: Closing,
    findings: Findings,
    urgencies: Urgencies,
    sources: Sources,
    frames: Frames,
    events: Events,
    projects: ProjectReads,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
) -> object:
    """Accept the proposal. `mint_work_item` mints a linked hub item from `body` (or the proposal's own), wrapped in
    the "Related findings" template when it names findings; false records the decline. Promotes nothing; a blank
    reason is stored as none. 404 unknown; 422 `body` or `urgency` without a mint, `urgency_unknown`/`urgency_retired`,
    or a blank minted title or body; 409 closed or a raced ingest; 503 the default graph retired."""
    proposal = _get_or_404(proposal_id, proposals)
    urgency = stated_urgency(request.urgency, urgencies)
    proposal_findings = resolve_proposal_findings(findings, proposal.findings)
    try:
        accepted = closing.accept(
            proposal,
            reason=request.reason,
            by=identity.user_id,
            body=request.body,
            mint=request.mint_work_item,
            findings=proposal_findings,
            urgency=urgency,
        )
    except (UrgencyUnknown, UrgencyRetired) as exc:  # retired after the edge's check
        raise urgency_refusal(exc) from exc
    except (GardenProposalMintFieldWithoutMint, WorkItemFieldBlank) as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_invalid_problem, str(exc))) from exc
    except GardenProposalAlreadyClosed as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_already_closed_problem, str(exc))) from exc
    except DefaultGraphRetired as exc:
        raise ProblemHTTPException(partial(problems_admin.default_graph_retired_problem, str(exc))) from exc
    except IngestConflict as exc:
        raise ingest_conflict_refusal(sources, exc) from exc
    if accepted.chunk_id is not None:
        # A freshly minted chunk rests `not_ready`, exactly as a `POST
        # /work-sources/hub/items` mint does.
        chunk_events.ChunkChanged.of(frames, accepted.chunk_id, prev_status=None).publish(
            cause="minted", key=f"chunks:{accepted.chunk_id}"
        )
        events.publish_queue_changed()  # mint adds the chunk to the backlog list
    return GardenProposalAcceptResponse(
        **_labelled(proposal, accepted.closure, projects).model_dump(), chunk_id=accepted.chunk_id
    )


def _create(
    request: GardenProposalCreateRequest,
    routines: IReadRoutineRepository,
    findings: IReadFindingRepository,
    authoring: GardenProposalAuthoring,
    identity: ResolvedIdentity,
    project: Project,
) -> GardenProposalView:
    routine = None
    if request.routine is not None:
        routine = routines.get_by_slug(project.project_id, request.routine)
        if routine is None:
            raise ProblemHTTPException(
                partial(problems_garden.routine_not_found_problem, f"unknown routine {request.routine!r}", 422)
            )
    linked = _resolve_findings_or_422(request.findings, findings)
    try:
        proposal = authoring.create_operator(
            project=project,
            created_by=identity.user_id,
            routine=routine,
            class_=request.class_,
            title=request.title,
            body=request.body,
            findings=linked,
        )
    except (
        DuplicateProposalFindingError,
        GardenProposalFindingExitedError,
        GardenProposalFindingOtherProjectError,
        GardenProposalRoutineOtherProjectError,
        GardenProposalBlankFieldError,
    ) as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_invalid_problem, str(exc))) from exc
    return garden_proposal_view(proposal, None, ProjectLabels.of_project(project).fields(proposal.project_id))


@router.post(
    "/garden-proposals",
    response_model=GardenProposalView,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
)
@inject_sync
def create_garden_proposal(
    request: GardenProposalCreateRequest,
    routines: Routines,
    findings: Findings,
    authoring: Authoring,
    projects: ProjectReads,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
) -> GardenProposalView:
    """Mint an operator-authored proposal in the call's project (the tenant's only one, else `422
    project_required`), naming `routine` when the caller names one, else none. 422 for a blank title/class/body,
    an unknown routine, or an unknown, exited, or duplicate finding id, or one of another project — the whole
    call is refused, nothing is linked. Deprecated: `POST /api/projects/{project}/garden-proposals`."""
    return _create(request, routines, findings, authoring, identity, project_of_call(projects))


@project_router.post(
    "/garden-proposals",
    response_model=GardenProposalView,
    status_code=status.HTTP_201_CREATED,
)
@inject_sync
def create_project_garden_proposal(
    request: GardenProposalCreateRequest,
    routines: Routines,
    findings: Findings,
    authoring: Authoring,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
    project: PathProject,
) -> GardenProposalView:
    """Mint an operator-authored proposal in the project, naming `routine` — one of the project's — when the
    caller names one, else none. 422 for a blank title/class/body, an unknown routine, or an unknown, exited, or
    duplicate finding id, or one of another project — the whole call is refused, nothing is linked. 404 on an
    unknown project."""
    return _create(request, routines, findings, authoring, identity, project)


@router.patch(
    "/garden-proposals/{proposal_id}",
    response_model=GardenProposalView,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
@inject_sync
def edit_garden_proposal(
    proposal_id: str,
    request: GardenProposalEditRequest,
    proposals: Proposals,
    closures: Closures,
    authoring: Authoring,
    projects: ProjectReads,
) -> GardenProposalView:
    """Replace the given fields of `{proposal_id}` in place, all-or-nothing
    — works on either origin while open. 404 unknown proposal, 409 already closed,
    422 a blank title/class/body or an edit naming no field."""
    proposal = _get_or_404(proposal_id, proposals)
    edit = GardenProposalEdit(
        title=request.title if request.title is not None else UNSET,
        class_=request.class_ if request.class_ is not None else UNSET,
        body=request.body if request.body is not None else UNSET,
    )
    try:
        updated = authoring.edit(proposal, edit)
    except (GardenProposalBlankFieldError, GardenProposalEmptyEditError) as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_invalid_problem, str(exc))) from exc
    except GardenProposalAlreadyClosed as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_already_closed_problem, str(exc))) from exc
    return _labelled(updated, closures.get(proposal_id), projects)


@router.post(
    "/garden-proposals/{proposal_id}/attach",
    response_model=GardenProposalView,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
@inject_sync
def attach_garden_proposal_findings(
    proposal_id: str,
    request: GardenProposalFindingsRequest,
    proposals: Proposals,
    closures: Closures,
    findings: Findings,
    authoring: Authoring,
    projects: ProjectReads,
) -> GardenProposalView:
    """Link the given finding ids to `{proposal_id}` — works on either origin
    while open. 404 unknown proposal, 409 already closed, 422 no finding id, an unknown,
    exited, or duplicate finding id, one of another project than the proposal's, or one
    already linked to this proposal — the whole call is refused, nothing is linked."""
    proposal = _get_or_404(proposal_id, proposals)
    _require_open_for(GardenProposalVerb.ATTACH, proposal, closures)
    linked = _resolve_findings_or_422(request.findings, findings)
    try:
        updated = authoring.attach(proposal, linked)
    except (
        GardenProposalNoFindingsError,
        DuplicateProposalFindingError,
        GardenProposalFindingExitedError,
        GardenProposalFindingOtherProjectError,
        GardenProposalFindingAlreadyLinkedError,
    ) as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_invalid_problem, str(exc))) from exc
    except GardenProposalAlreadyClosed as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_already_closed_problem, str(exc))) from exc
    return _labelled(updated, closures.get(proposal_id), projects)


@router.post(
    "/garden-proposals/{proposal_id}/detach",
    response_model=GardenProposalView,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
@inject_sync
def detach_garden_proposal_findings(
    proposal_id: str,
    request: GardenProposalFindingsRequest,
    proposals: Proposals,
    closures: Closures,
    findings: Findings,
    authoring: Authoring,
    projects: ProjectReads,
) -> GardenProposalView:
    """Unlink the given finding ids from `{proposal_id}` — works on either
    origin while open. 404 unknown proposal, 409 already closed, 422 no finding id, an
    unknown or duplicate id, or one not linked to this proposal."""
    proposal = _get_or_404(proposal_id, proposals)
    _require_open_for(GardenProposalVerb.DETACH, proposal, closures)
    linked = _resolve_findings_or_422(request.findings, findings)
    try:
        updated = authoring.detach(proposal, linked)
    except (GardenProposalNoFindingsError, DuplicateProposalFindingError, GardenProposalFindingNotLinkedError) as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_invalid_problem, str(exc))) from exc
    except GardenProposalAlreadyClosed as exc:
        raise ProblemHTTPException(partial(problems_garden.garden_proposal_already_closed_problem, str(exc))) from exc
    return _labelled(updated, closures.get(proposal_id), projects)
