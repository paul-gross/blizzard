"""Cadence routes — create, list, read, edit, retire, and enable.

A cadence's slug is unique only within its project: the create and the project's listing sit under
``/api/projects/{project}``; the un-nested listing reads the whole tenant or the project ``?project=`` names. Routes
keyed by ``cadence_id`` need no project, since the id settles it.

The controller stays read-only (``bzh:controller-read-only``), resolving a ``cadence_id`` before delegating to the
domain. Every write needs ``graph:edit``, carries the request's :class:`ChangeContext` and an optional ``If-Match``
(``bzh:configured-record``), and tells the domain whether the caller may admit work, which a write leaving the
cadence promoting its runs needs. Every view carries the cadence's attention, resolved in bulk."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import problems, problems_garden
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.project_label_fields import ProjectLabelFields
from blizzard.hub.api.project_labels import ProjectLabels
from blizzard.hub.api.project_params import PathProject, QueryProject
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.permissions import Permission, may_admit
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.cadence.model import (
    ALL_SCOPES,
    Anchor,
    Attention,
    Cadence,
    CadenceAdmissionRefused,
    CadenceEdit,
    CadenceShape,
    CadenceSlugTakenError,
    Chore,
    ChoreShape,
    ScheduleShape,
    ScopeSelection,
)
from blizzard.hub.domain.cadence.services import CadenceView as CadenceRead
from blizzard.hub.domain.cadence.services import ErrandView
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.hub.domain.projects.model import Project
from blizzard.wire.cadence import (
    CadenceAttentionView,
    CadenceCreateRequest,
    CadenceDocument,
    CadenceEditRequest,
    CadenceErrandView,
    CadenceLifecycleRequest,
    CadenceScheduleDocument,
    CadenceScheduleView,
    CadenceView,
    ChoreDocument,
    ChoreView,
    ErrandCountsView,
    ErrandRunView,
)

_GUARDS = [Depends(reject_runner_principal), Depends(tenant_bound)]
router = APIRouter(prefix="/api", tags=["cadences"], dependencies=_GUARDS)
project_router = APIRouter(prefix="/api/projects/{project}", tags=["cadences"], dependencies=_GUARDS)

_EDIT = Depends(require(Permission.GRAPH_EDIT))


def schedule_shape(schedule: CadenceScheduleDocument | None) -> ScheduleShape | None:
    """The schedule table a request or document entry states, ``None`` when it states none."""
    return None if schedule is None else ScheduleShape(schedule.cron, schedule.zone)


def chore_shapes(chores: Sequence[ChoreDocument]) -> tuple[ChoreShape, ...]:
    """The chores a request or document entry states, in the order written."""
    return tuple(
        ChoreShape(
            key=chore.key,
            routine=chore.routine,
            scopes=chore.scopes,
            mode=None if chore.mode is None else chore.mode.value,
            note=chore.note,
            promote=chore.promote,
        )
        for chore in chores
    )


def cadence_shape(document: CadenceDocument) -> CadenceShape:
    """The create shape a request or document entry states."""
    return CadenceShape(
        slug=document.slug,
        name=document.name,
        schedule=schedule_shape(document.schedule),
        spacing=document.spacing,
        anchor=document.anchor,
        chores=chore_shapes(document.chores),
    )


def chore_scopes(chore: Chore) -> Literal["all"] | list[str] | None:
    """A chore's scopes in the shape a document writes them: ``"all"``, the listed slugs, or ``None``."""
    selection = chore.spec.selection
    if selection is ScopeSelection.ALL:
        return ALL_SCOPES
    return list(chore.spec.scopes) if selection is ScopeSelection.LISTED else None


def _attention_view(item: Attention) -> CadenceAttentionView:
    return CadenceAttentionView(reason=item.reason, chore=item.chore, scope=item.scope)


def _errand_view(row: ErrandView) -> CadenceErrandView:
    run = None if row.last_run is None else row.last_run.run
    holder = None if run is None else run.holder
    return CadenceErrandView(
        chore=row.errand.chore.key,
        scope=row.errand.scope_slug,
        state=row.state,
        attention=[_attention_view(a) for a in row.stopping],
        last_run=None
        if run is None
        else ErrandRunView(
            chunk_id=None if holder is None else holder.chunk_id,
            status=None if holder is None else holder.status,
            scheduled_at=iso_utc(run.minted_at),
            started_at=None if holder is None or holder.started_at is None else iso_utc(holder.started_at),
            finished_at=None if (finished := run.anchored_at(Anchor.FINISHED)) is None else iso_utc(finished),
        ),
        due_at=None if row.due_at is None else iso_utc(row.due_at),
    )


def _cadence_view(read: CadenceRead, labels: ProjectLabelFields) -> CadenceView:
    cadence = read.cadence
    rhythm = cadence.rhythm
    anchor = rhythm.effective_anchor()
    return CadenceView(
        cadence_id=cadence.cadence_id,
        slug=cadence.slug,
        name=cadence.name,
        schedule=None
        if rhythm.schedule is None
        else CadenceScheduleView(cron=rhythm.schedule.cron, zone=rhythm.schedule.zone),
        spacing_minutes=None if rhythm.spacing is None else rhythm.spacing.minutes,
        anchor=None if anchor is None else anchor.value,
        chores=[
            ChoreView(
                chore_id=chore.chore_id,
                key=chore.key,
                routine=chore.spec.routine_slug,
                scopes=chore_scopes(chore),
                mode=chore.spec.mode,
                note=chore.spec.note,
                promote=chore.spec.promote,
                scopes_set_at=iso_utc(chore.scopes_set_at),
            )
            for chore in cadence.chores
        ],
        attention=[_attention_view(a) for a in read.attention],
        errand_counts=ErrandCountsView(**{state.value: count for state, count in read.errand_counts.items()}),
        next_moments=[iso_utc(moment) for moment in read.next_moments],
        created_at=iso_utc(cadence.created_at),
        retired=cadence.retired,
        revision=cadence.revision,
        **labels,
    )


def _labelled(cadence: Cadence, services: TenantServices) -> CadenceView:
    """``cadence``'s view as a write left it, labelled from one read of its project."""
    read = services.cadence_reading.view(cadence)
    return _cadence_view(read, ProjectLabels.read(services, [cadence.project_id]).fields(cadence.project_id))


def _listing(services: TenantServices, project_id: str | None, *, include_retired: bool) -> list[CadenceView]:
    reads = services.cadence_reading.list_cadences(project_id, include_retired=include_retired)
    labels = ProjectLabels.read(services, (r.cadence.project_id for r in reads))
    return [_cadence_view(r, labels.fields(r.cadence.project_id)) for r in reads]


def _unprocessable(exc: ConfigFieldError) -> HTTPException:
    return ProblemHTTPException(partial(problems_garden.config_field_invalid_problem, exc.field, exc.message))


def _revision_conflict(exc: ConfigRevisionConflict) -> HTTPException:
    return ProblemHTTPException(partial(problems.revision_conflict_problem, str(exc)))


def admission_refused(exc: CadenceAdmissionRefused) -> HTTPException:
    """403 for a write leaving a cadence promoting its runs, by a caller who may not admit work."""
    return ProblemHTTPException(partial(problems_garden.cadence_admission_refused_problem, str(exc)))


def _cadence_or_404(cadence_id: str, services: TenantServices) -> Cadence:
    cadence = services.cadences.get(cadence_id)
    if cadence is None:
        raise ProblemHTTPException(partial(problems_garden.cadence_not_found_problem, f"unknown cadence {cadence_id}"))
    return cadence


def _create(
    request: CadenceCreateRequest,
    identity: ResolvedIdentity,
    door: RequestDoor,
    services: TenantServices,
    project: Project,
) -> CadenceView:
    try:
        cadence = services.cadence_authoring.create(
            cadence_shape(request),
            project=project,
            ctx=change_context(identity, door),
            may_admit=may_admit(identity.permissions),
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except CadenceSlugTakenError as exc:
        raise ProblemHTTPException(partial(problems_garden.cadence_slug_taken_problem, str(exc))) from exc
    except CadenceAdmissionRefused as exc:
        raise admission_refused(exc) from exc
    read = services.cadence_reading.view(cadence)
    return _cadence_view(read, ProjectLabels.of_project(project).fields(cadence.project_id))


@project_router.post("/cadences", response_model=CadenceView, status_code=status.HTTP_201_CREATED)
def create_project_cadence(
    request: CadenceCreateRequest,
    identity: Annotated[ResolvedIdentity, _EDIT],
    door: RequestDoor,
    services: Services,
    project: PathProject,
) -> CadenceView:
    """Create a cadence in the project. 422 naming the field on a shape that cannot mean anything — no schedule and
    no spacing, a malformed cron, zone, spacing, anchor, or chore — or on a slug the project already holds; 403 when
    it would keep a promoting chore and the caller may not promote chunks; 404 on an unknown project. A chore whose
    routine or scopes do not resolve is accepted, and shown in the cadence's `attention`."""
    return _create(request, identity, door, services, project)


@router.get("/cadences", response_model=list[CadenceView], dependencies=[Depends(require(Permission.FLEET_VIEW))])
def list_cadences(
    services: Services,
    project: QueryProject,
    include_retired: Annotated[bool, Query()] = False,
) -> list[CadenceView]:
    """Every cadence with its attention, newest first — of the project `?project=` names, else of every project; a
    retired cadence excluded by default, included and marked when `include_retired`."""
    return _listing(services, project, include_retired=include_retired)


@project_router.get(
    "/cadences", response_model=list[CadenceView], dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
def list_project_cadences(
    services: Services,
    project: PathProject,
    include_retired: Annotated[bool, Query()] = False,
) -> list[CadenceView]:
    """Every cadence of the project with its attention, newest first — a retired cadence excluded by default,
    included and marked when `include_retired`; 404 on an unknown project."""
    return _listing(services, project.project_id, include_retired=include_retired)


@router.get(
    "/cadences/{cadence_id}", response_model=CadenceView, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
def get_cadence(cadence_id: str, services: Services) -> CadenceView:
    """One cadence's whole record with its attention; 404 on an unknown id."""
    read = services.cadence_reading.show(cadence_id)
    if read is None:
        raise ProblemHTTPException(partial(problems_garden.cadence_not_found_problem, f"unknown cadence {cadence_id}"))
    labels = ProjectLabels.read(services, [read.cadence.project_id])
    return _cadence_view(read, labels.fields(read.cadence.project_id))


@router.get(
    "/cadences/{cadence_id}/errands",
    response_model=list[CadenceErrandView],
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def list_cadence_errands(cadence_id: str, services: Services) -> list[CadenceErrandView]:
    """Every errand of the cadence — each chore over each scope — with its state (`due`, `running`, `waiting`, or
    `stopped`), the attention that stops it, its last run, and when it is next due (`due_at`, null while running
    or stopped), chore by chore in cadence order and by scope slug within a chore; 404 on an unknown id. A retired
    cadence's errands are all `stopped`."""
    cadence = _cadence_or_404(cadence_id, services)
    return [_errand_view(row) for row in services.cadence_reading.errands(cadence)]


def _cadence_edit(request: CadenceEditRequest) -> CadenceEdit:
    """The sparse edit ``request`` states — only its present fields; ``chores`` may not be cleared."""
    stated = request.model_fields_set
    if "chores" in stated and request.chores is None:
        raise ConfigFieldError("chores", "must not be null")
    return CadenceEdit(
        name=request.name if "name" in stated else UNSET,
        schedule=schedule_shape(request.schedule) if "schedule" in stated else UNSET,
        spacing=request.spacing if "spacing" in stated else UNSET,
        anchor=request.anchor if "anchor" in stated else UNSET,
        chores=UNSET if request.chores is None else chore_shapes(request.chores),
    )


@router.patch("/cadences/{cadence_id}", response_model=CadenceView)
def edit_cadence(
    cadence_id: str,
    request: CadenceEditRequest,
    identity: Annotated[ResolvedIdentity, _EDIT],
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> CadenceView:
    """Apply only the fields present — the name, the schedule, the spacing, the anchor, and the whole chore list,
    matched to the stored chores by key so a chore whose key stays keeps its id; an explicit `null` clears
    `schedule`, `spacing`, or `anchor`. An edit that changes nothing writes nothing. 404 on an unknown id; 422
    naming the field on a `null` name or chore list, or a merged shape that cannot mean anything; 403 when the
    cadence would stay live keeping a promoting chore and the caller may not promote chunks; 409 on a stale
    `If-Match`."""
    cadence = _cadence_or_404(cadence_id, services)
    try:
        edited = services.cadence_authoring.edit(
            cadence,
            _cadence_edit(request),
            change_context(identity, door),
            may_admit=may_admit(identity.permissions),
            if_match=if_match,
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    except CadenceAdmissionRefused as exc:
        raise admission_refused(exc) from exc
    return _labelled(edited, services)


@router.post("/cadences/{cadence_id}/retire", response_model=CadenceView, status_code=status.HTTP_202_ACCEPTED)
def retire_cadence(
    cadence_id: str,
    request: CadenceLifecycleRequest,
    identity: Annotated[ResolvedIdentity, _EDIT],
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> CadenceView:
    """Retire a cadence — a reversible brake: a retired cadence mints nothing. Retiring a retired cadence writes
    nothing. 404 on an unknown id, 409 on a stale `If-Match`."""
    cadence = _cadence_or_404(cadence_id, services)
    try:
        retired = services.cadence_lifecycle.retire(
            cadence, change_context(identity, door), by=request.by, if_match=if_match
        )
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return _labelled(retired, services)


@router.post("/cadences/{cadence_id}/enable", response_model=CadenceView, status_code=status.HTTP_202_ACCEPTED)
def enable_cadence(
    cadence_id: str,
    request: CadenceLifecycleRequest,
    identity: Annotated[ResolvedIdentity, _EDIT],
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> CadenceView:
    """Re-enable a retired cadence; enabling an enabled one writes nothing. 404 on an unknown id; 403 when it keeps
    a promoting chore and the caller may not promote chunks; 409 on a stale `If-Match`."""
    cadence = _cadence_or_404(cadence_id, services)
    try:
        enabled = services.cadence_lifecycle.enable(
            cadence,
            change_context(identity, door),
            by=request.by,
            may_admit=may_admit(identity.permissions),
            if_match=if_match,
        )
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    except CadenceAdmissionRefused as exc:
        raise admission_refused(exc) from exc
    return _labelled(enabled, services)
