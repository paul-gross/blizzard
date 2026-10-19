"""Routine routes — create, list, read, edit, retire, enable, run, trend, and sweep.

A routine's name is unique only within its project: the create and the project's listing sit under
``/api/projects/{project}``. The un-nested listing reads the whole tenant or the project ``?project=``
names; the un-nested create stays as a deprecated alias acting in the call's project. Routes keyed by
``routine_id`` keep their paths, since the id settles the project.

The controller stays read-only (``bzh:controller-read-only``), resolving a ``routine_id``
before delegating to the domain. Every write carries the request's :class:`ChangeContext`
and an optional ``If-Match`` (``bzh:configured-record``). ``GET /routines/trend`` is declared ahead of ``GET
/routines/{routine_id}`` so the literal path wins; ``sweeps`` nests under a resolved id."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from blizzard.auth_core import Permission
from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.chunk_views import ingest_conflict_response
from blizzard.hub.api.deps import Services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.project_label_fields import ProjectLabelFields
from blizzard.hub.api.project_labels import ProjectLabels
from blizzard.hub.api.project_params import CallProject, PathProject, QueryProject, project_of_call
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.chunk.ingest import IngestConflict
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.config.work_sources import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.garden.findings.trend import Trend, TrendWindow
from blizzard.hub.domain.garden.proposals.model import GardenProposalCounts
from blizzard.hub.domain.garden.routines import (
    Routine,
    RoutineDefaultScopeUnlinkError,
    RoutineEdit,
    RoutineGraphUnresolvedError,
    RoutineNameImmutableError,
    RoutineNameTakenError,
    RoutineScopeOtherProject,
)
from blizzard.hub.domain.garden.runs.baselines import RoutineBaseline
from blizzard.hub.domain.garden.runs.run import RoutineRetiredError, RunResult, ScopeNotRelatedError, ScopeRetiredError
from blizzard.hub.domain.garden.runs.sweeps import GardenSweeps, SweepWindow
from blizzard.hub.domain.garden.runs.window import InvalidWindowError, require_until_after_since
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlug, ScopeSlugError
from blizzard.hub.domain.graph.harnesses import InvalidHarnesses
from blizzard.hub.domain.projects.model import Project
from blizzard.wire.garden_proposal_counts import GardenProposalCountsRowView, GardenProposalCountsView
from blizzard.wire.garden_sweeps import GardenSweepsView, MeasurementReadingView, ScopeSweepView
from blizzard.wire.garden_trend import TrendAgeView, TrendPeriodView, TrendView
from blizzard.wire.routine import (
    RoutineBaselineRepoView,
    RoutineBaselineView,
    RoutineCreateRequest,
    RoutineEditRequest,
    RoutineLifecycleRequest,
    RoutineRunRequest,
    RoutineRunResponse,
    RoutineView,
)

_GUARDS = [Depends(reject_runner_principal), Depends(tenant_bound)]
router = APIRouter(prefix="/api", tags=["routines"], dependencies=_GUARDS)
project_router = APIRouter(prefix="/api/projects/{project}", tags=["routines"], dependencies=_GUARDS)


def _routine_view(routine: Routine, labels: ProjectLabelFields) -> RoutineView:
    return RoutineView(
        routine_id=routine.routine_id,
        name=routine.name,
        graph_name=routine.graph_name,
        default_scope_slug=routine.default_scope_slug,
        default_model=list(routine.default_model),
        default_effort=routine.default_effort,
        default_harnesses=list(routine.default_harnesses),
        created_at=iso_utc(routine.created_at),
        retired=routine.retired,
        revision=routine.revision,
        **labels,
    )


def _labelled(routine: Routine, services: TenantServices) -> RoutineView:
    """``routine``'s view, labelled from one read of its project."""
    return _routine_view(routine, ProjectLabels.read(services, [routine.project_id]).fields(routine.project_id))


def _listing(services: TenantServices, project_id: str | None, *, include_retired: bool) -> list[RoutineView]:
    routines = [r for r in services.routines.list_all(project_id) if include_retired or not r.retired]
    labels = ProjectLabels.read(services, (r.project_id for r in routines))
    return [_routine_view(r, labels.fields(r.project_id)) for r in routines]


def _unprocessable(exc: ConfigFieldError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", exc.field], "msg": exc.message, "type": "value_error"}],
    )


def _revision_conflict(exc: ConfigRevisionConflict) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def _routine_or_404(routine_id: str, services: TenantServices) -> Routine:
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    return routine


def _create(
    request: RoutineCreateRequest,
    identity: ResolvedIdentity,
    door: RequestDoor,
    services: TenantServices,
    project: Project,
) -> RoutineView:
    try:
        slug = ScopeSlug.parse(request.default_scope_slug)
        routine = services.routine_authoring.create(
            project_id=project.project_id,
            name=request.name,
            graph_name=request.graph_name,
            default_scope_slug=slug,
            ctx=change_context(identity, door),
            default_model=request.default_model,
            default_effort=request.default_effort,
            default_harnesses=request.default_harnesses,
        )
    except (
        ScopeSlugError,
        RoutineNameTakenError,
        RoutineGraphUnresolvedError,
        RoutineScopeOtherProject,
        InvalidHarnesses,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return _routine_view(routine, ProjectLabels.of_project(project).fields(routine.project_id))


@router.post("/routines", response_model=RoutineView, status_code=status.HTTP_201_CREATED, deprecated=True)
def create_routine(
    request: RoutineCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    project: CallProject,
) -> RoutineView:
    """Mint a routine in the call's project, its default scope resolved or minted there; 422 on a duplicate name
    in the project, a malformed default scope slug, or a graph name with no enabled mint. Deprecated: `POST
    /api/projects/{project}/routines`."""
    return _create(request, identity, door, services, project)


@project_router.post("/routines", response_model=RoutineView, status_code=status.HTTP_201_CREATED)
def create_project_routine(
    request: RoutineCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    project: PathProject,
) -> RoutineView:
    """Mint a routine in the project, its default scope resolved or minted there; 422 on a duplicate name in the
    project, a malformed default scope slug, or a graph name with no enabled mint; 404 on an unknown project."""
    return _create(request, identity, door, services, project)


@router.get("/routines", response_model=list[RoutineView], dependencies=[Depends(require(Permission.FLEET_VIEW))])
def list_routines(
    services: Services,
    project: QueryProject,
    include_retired: Annotated[bool, Query()] = False,
) -> list[RoutineView]:
    """Every routine, newest first — of the project `?project=` names, else of every project; a retired routine
    excluded by default, included and marked when ``include_retired``."""
    return _listing(services, project, include_retired=include_retired)


@project_router.get(
    "/routines", response_model=list[RoutineView], dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
def list_project_routines(
    services: Services,
    project: PathProject,
    include_retired: Annotated[bool, Query()] = False,
) -> list[RoutineView]:
    """Every routine of the project, newest first — a retired routine excluded by default, included and marked
    when ``include_retired``; 404 on an unknown project."""
    return _listing(services, project.project_id, include_retired=include_retired)


def _parse_instant(value: str, *, field: str) -> datetime:
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field} {value!r} is not a valid ISO-8601 instant",
        ) from exc


def _trend_view(trend: Trend) -> TrendView:
    return TrendView(
        routine_name=trend.routine_name,
        since=iso_utc(trend.since),
        until=iso_utc(trend.until),
        period_days=trend.period_days,
        periods=[
            TrendPeriodView(
                period_start=iso_utc(p.period_start),
                period_end=iso_utc(p.period_end),
                created=p.created,
                exits=p.exits,
                outflow=p.outflow,
                withdrawn=p.withdrawn,
                reopened=p.reopened,
            )
            for p in trend.periods
        ],
        age=TrendAgeView(
            boundary=iso_utc(trend.age.boundary),
            recent=trend.age.recent,
            older=trend.age.older,
            unattributed=trend.age.unattributed,
        ),
    )


@router.get("/routines/trend", response_model=TrendView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def routine_trend(
    services: Services,
    project: QueryProject,
    routine: Annotated[str, Query()],
    since: Annotated[str, Query()],
    until: Annotated[str, Query()],
    introduced_boundary: Annotated[str, Query()],
    period_days: Annotated[int, Query()] = 7,
) -> TrendView:
    """`routine`'s finding inflow-against-outflow over `[since, until)`: per
    `period_days`-wide period, findings created and per-kind exit counts, the outflow/
    withdrawn roll-ups, and the age cut against `introduced_boundary`. `routine` is read in the project
    `?project=` names, else in the call's project (the tenant's only one, else `422 project_required`). 404 on an
    unknown routine name in that project; 422 on a malformed instant, a non-positive `period_days`, a
    non-positive span, or a span/`period_days` pair bucketing past the period cap."""
    project_id = project if project is not None else project_of_call(services).project_id
    if services.routines.get_by_name(project_id, routine) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine!r}")
    parsed_since = _parse_instant(since, field="since")
    parsed_until = _parse_instant(until, field="until")
    parsed_boundary = _parse_instant(introduced_boundary, field="introduced_boundary")
    try:
        window = TrendWindow.of(
            since=parsed_since, until=parsed_until, introduced_boundary=parsed_boundary, period_days=period_days
        )
    except InvalidWindowError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return _trend_view(services.garden_trend.trend(project_id, routine, window))


def _proposal_counts_row_view(counts: GardenProposalCounts, labels: ProjectLabelFields) -> GardenProposalCountsRowView:
    # `class_`'s alias is the Python keyword `class` — constructed by alias via
    # `model_validate`, the `src/blizzard/hub/api/garden_runs.py` `_set_delta_view` shape.
    return GardenProposalCountsRowView.model_validate(
        {
            "origin": counts.origin,
            "routine_name": counts.routine_name,
            "class": counts.class_,
            "open": counts.open,
            "passed": counts.passed,
            "accepted_with_item": counts.accepted_with_item,
            "accepted_without_item": counts.accepted_without_item,
            "created": counts.created,
            **labels,
        }
    )


@router.get(
    "/routines/proposal-counts",
    response_model=GardenProposalCountsView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def routine_proposal_counts(
    services: Services,
    project: QueryProject,
    since: Annotated[str, Query()],
    until: Annotated[str, Query()],
    routine: Annotated[str | None, Query()] = None,
    origin: Annotated[GardenProposalOrigin | None, Query()] = None,
) -> GardenProposalCountsView:
    """Garden-proposal counts per origin, routine, and class over
    `[since, until)`, split into open/passed/accepted-with-item/accepted-without-item —
    `created` is their sum. `routine` narrows to one routine's rows of
    both origins when given, looked up in the project `?project=` names, else in the call's project (the tenant's
    only one, else `422 project_required`); 404 on an unknown one. `?project=` alone narrows to that project's
    rows; with neither, every project's rows are counted, one row per project. `origin` narrows to one origin.
    422 on a malformed instant or `until <= since`."""
    parsed_since = _parse_instant(since, field="since")
    parsed_until = _parse_instant(until, field="until")
    try:
        require_until_after_since(parsed_since, parsed_until)
    except InvalidWindowError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    project_id: str | None = project
    if routine is not None:
        if project_id is None:
            project_id = project_of_call(services).project_id
        if services.routines.get_by_name(project_id, routine) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine!r}")
    rows = services.garden_proposals.counts_by_class(
        since=parsed_since, until=parsed_until, routine_name=routine, project_id=project_id, origin=origin
    )
    labels = ProjectLabels.read(services, (c.project_id for c in rows))
    return GardenProposalCountsView(
        since=iso_utc(parsed_since),
        until=iso_utc(parsed_until),
        routine=routine,
        origin=origin,
        rows=[_proposal_counts_row_view(c, labels.fields(c.project_id)) for c in rows],
    )


@router.get(
    "/routines/{routine_id}", response_model=RoutineView, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
def get_routine(routine_id: str, services: Services) -> RoutineView:
    """One routine's whole record; 404 on an unknown id."""
    return _labelled(_routine_or_404(routine_id, services), services)


def _baseline_view(baseline: RoutineBaseline) -> RoutineBaselineView:
    return RoutineBaselineView(
        scope_slug=baseline.scope_slug,
        finding_set_id=baseline.finding_set_id,
        recorded_at=iso_utc(baseline.recorded_at),
        repos=[
            RoutineBaselineRepoView(repo=r.repo, revision=r.revision, landed_since=r.landed_since)
            for r in baseline.repos
        ],
    )


@router.get(
    "/routines/{routine_id}/baselines",
    response_model=list[RoutineBaselineView],
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def routine_baselines(routine_id: str, services: Services) -> list[RoutineBaselineView]:
    """Every scope `routine_id` has swept; a scope it never swept has no row.
    404 on an unknown routine id."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    return [_baseline_view(b) for b in services.routine_baselines.baselines_for(routine)]


@router.get(
    "/routines/{routine_id}/scopes",
    response_model=list[str],
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def list_routine_scopes(routine_id: str, services: Services) -> list[str]:
    """Every scope slug linked to `routine_id`, sorted — its own default
    scope is always among them. 404 on an unknown routine id."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    return services.routine_scopes.list_scopes(routine.routine_id)


def _resolve_scope_for_membership(routine: Routine, scope_slug: str, services: TenantServices) -> Scope:
    """Parse and resolve `scope_slug` within `routine`'s project for a link/unlink write: 422 on a malformed
    slug, 404 on a well-formed one the project does not hold — a management verb never silently mints
    (`hub scope create` is the one deliberate mint path)."""
    try:
        slug = ScopeSlug.parse(scope_slug)
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    scope = services.scopes.get(routine.project_id, slug.value)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown scope {scope_slug}")
    return scope


@router.put("/routines/{routine_id}/scopes/{scope_slug}", status_code=status.HTTP_204_NO_CONTENT)
def link_routine_scope(
    routine_id: str,
    scope_slug: str,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> Response:
    """Link `scope_slug` into `routine_id`'s own set — a routine edit of its `scopes`;
    linking a linked scope writes nothing. 404 on an unknown routine id or a well-formed but
    unknown scope slug; 422 on a malformed scope slug; 409 on a stale `If-Match`."""
    routine = _routine_or_404(routine_id, services)
    scope = _resolve_scope_for_membership(routine, scope_slug, services)
    try:
        services.routine_scope_membership.link(routine, scope, change_context(identity, door), if_match=if_match)
    except RoutineScopeOtherProject as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/routines/{routine_id}/scopes/{scope_slug}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_routine_scope(
    routine_id: str,
    scope_slug: str,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> Response:
    """Unlink `scope_slug` from `routine_id`'s own set — a routine edit of its `scopes`;
    unlinking an unlinked scope writes nothing. 404 on an unknown routine id or a well-formed
    but unknown scope slug; 422 on a malformed scope slug, or on naming the routine's own
    default scope — always a member of its own set; 409 on a stale `If-Match`."""
    routine = _routine_or_404(routine_id, services)
    scope = _resolve_scope_for_membership(routine, scope_slug, services)
    try:
        services.routine_scope_membership.unlink(routine, scope, change_context(identity, door), if_match=if_match)
    except (RoutineDefaultScopeUnlinkError, RoutineScopeOtherProject) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _routine_edit(request: RoutineEditRequest) -> RoutineEdit:
    """The sparse edit ``request`` states — only its present fields, the default scope slug parsed."""
    present = {name: getattr(request, name) for name in request.model_fields_set}
    slug = present.get("default_scope_slug")
    if slug is not None:
        present["default_scope_slug"] = ScopeSlug.parse(slug)
    return RoutineEdit(**present)


@router.patch("/routines/{routine_id}", response_model=RoutineView)
def edit_routine(
    routine_id: str,
    request: RoutineEditRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> RoutineView:
    """Apply only the fields present — the graph, the default scope, and the model, effort,
    and harness defaults; an explicit `null` clears `default_effort`. A present `name` must
    restate the current one. An edit that changes nothing writes nothing. 404 on an unknown
    id; 422 on a name change, a `null` field, a malformed default scope slug, or a graph name
    with no enabled mint; 409 on a stale `If-Match` naming the current revision."""
    routine = _routine_or_404(routine_id, services)
    try:
        edited = services.routine_authoring.edit(
            routine, _routine_edit(request), change_context(identity, door), if_match=if_match
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    except (
        ScopeSlugError,
        RoutineNameImmutableError,
        RoutineGraphUnresolvedError,
        RoutineScopeOtherProject,
        InvalidHarnesses,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return _labelled(edited, services)


@router.post("/routines/{routine_id}/retire", response_model=RoutineView, status_code=status.HTTP_202_ACCEPTED)
def retire_routine(
    routine_id: str,
    request: RoutineLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> RoutineView:
    """Retire a routine — a reversible brake; retiring a retired routine writes nothing.
    404 on an unknown id, 409 on a stale `If-Match`."""
    routine = _routine_or_404(routine_id, services)
    try:
        retired = services.routine_lifecycle.retire(
            routine, change_context(identity, door), by=request.by, if_match=if_match
        )
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return _labelled(retired, services)


@router.post("/routines/{routine_id}/enable", response_model=RoutineView, status_code=status.HTTP_202_ACCEPTED)
def enable_routine(
    routine_id: str,
    request: RoutineLifecycleRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.GRAPH_EDIT))],
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> RoutineView:
    """Re-enable a retired routine; enabling an enabled one writes nothing. 404 on an
    unknown id, 409 on a stale `If-Match`."""
    routine = _routine_or_404(routine_id, services)
    try:
        enabled = services.routine_lifecycle.enable(
            routine, change_context(identity, door), by=request.by, if_match=if_match
        )
    except ConfigRevisionConflict as exc:
        raise _revision_conflict(exc) from exc
    return _labelled(enabled, services)


def _sweeps_view(sweeps: GardenSweeps) -> GardenSweepsView:
    return GardenSweepsView(
        routine_name=sweeps.routine_name,
        since=iso_utc(sweeps.since),
        until=iso_utc(sweeps.until),
        last_swept=[
            ScopeSweepView(
                scope_slug=s.scope_slug,
                finding_set_id=s.finding_set_id,
                produced_at=iso_utc(s.produced_at) if s.produced_at is not None else None,
                revisions=s.revisions,
            )
            for s in sweeps.last_swept
        ],
        measurements=[
            MeasurementReadingView(
                scope_slug=m.scope_slug, produced_at=iso_utc(m.produced_at), measurement=m.measurement
            )
            for m in sweeps.measurements
        ],
    )


@router.get(
    "/routines/{routine_id}/sweeps",
    response_model=GardenSweepsView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def routine_sweeps(
    routine_id: str,
    services: Services,
    since: Annotated[str, Query()],
    until: Annotated[str, Query()],
) -> GardenSweepsView:
    """``routine_id``'s per-scope last-swept table — the routine's declared
    set, retired scopes filtered out unless already swept while linked — and its
    measurement series over ``[since, until)``. 404 on an unknown id; 422 on a
    malformed instant or a non-positive span."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    parsed_since = _parse_instant(since, field="since")
    parsed_until = _parse_instant(until, field="until")
    try:
        window = SweepWindow.of(parsed_since, parsed_until)
    except InvalidWindowError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    sweeps = services.garden_sweeps.sweeps(routine, since=window.since, until=window.until)
    return _sweeps_view(sweeps)


def _run_response(result: RunResult) -> RoutineRunResponse:
    baseline = result.baseline
    return RoutineRunResponse(
        chunk_id=result.chunk_id,
        source=result.item.source,
        ref=result.item.ref,
        title=result.item.title,
        body=result.item.body,
        routine_name=result.item.routine_name or "",
        scope_slug=result.item.scope_slug or "",
        effective_mode=result.effective_mode,
        downgraded=result.downgraded,
        baseline_finding_set_id=baseline.finding_set_id if baseline is not None else None,
        baseline_revisions=dict(baseline.revisions) if baseline is not None else None,
        created_at=iso_utc(result.item.created_at),
    )


@router.post(
    "/routines/{routine_id}/run",
    response_model=RoutineRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def run_routine(
    routine_id: str,
    request: RoutineRunRequest,
    services: Services,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
) -> object:
    """Mint and ingest a hub work item from the routine, in one act; its chunk rests ``not_ready``
    until promoted.
    422 on an unknown ``mode`` (request validation, before any lookup); 404 on an unknown id;
    422 on a malformed ``scope_slug``, or an effective scope no scope row holds or outside the
    routine's own related set (never minted); 503 on a retired routine
    (checked before the scope is even parsed), a retired effective
    scope, or a graph name with no
    enabled mint (mirroring ``POST /work-sources/{source}/items``'s own
    retired-default-graph shape); 409 on an out-of-band ingest already holding the
    allocated ref's pointer."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    try:
        services.routine_run.refuse_if_retired(routine)
    except RoutineRetiredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    try:
        slug = routine.effective_scope_slug(
            ScopeSlug.parse(request.scope_slug) if request.scope_slug is not None else None
        )
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    scope = services.scopes.get(routine.project_id, slug.value)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"unknown scope {slug.value!r}")
    try:
        result = services.routine_run.run(
            routine,
            scope=scope,
            mode=request.mode,
            note=request.note,
            author=WorkItemAuthor.user(identity.user_id),
        )
    except ScopeNotRelatedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"scope {exc.slug!r} is not related to routine {exc.routine_id!r} — link it first with "
                f"`blizzard hub routine scope add {exc.routine_id} {exc.slug}`"
            ),
        ) from exc
    except (RoutineGraphUnresolvedError, ScopeRetiredError, RoutineRetiredError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except IngestConflict as exc:
        return ingest_conflict_response(services, exc)
    # The chunk is minted `not_ready`, so its post-write status reads `not_ready`.
    chunk_events.ChunkChanged.of(services, result.chunk_id, prev_status=None).publish(
        cause="minted", key=f"chunks:{result.chunk_id}"
    )
    services.events.publish_queue_changed()  # the mint adds the chunk to the backlog list
    return _run_response(result)
