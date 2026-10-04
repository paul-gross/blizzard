"""Routine routes — create, list, read, edit, retire, enable, run, trend, and sweep.

The controller stays read-only (``bzh:controller-read-only``), resolving a ``routine_id``
before delegating to the domain. ``GET /routines/trend`` is declared ahead of ``GET
/routines/{routine_id}`` so the literal path wins; ``sweeps`` nests under a resolved id."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import CHUNK_CONTROL, FLEET_VIEW, GRAPH_EDIT
from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.chunk.ingest import IngestConflict
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.garden.findings.trend import InvalidTrendWindow, Trend, TrendWindow
from blizzard.hub.domain.garden.proposals.model import GardenProposalCounts
from blizzard.hub.domain.garden.routines import (
    Routine,
    RoutineDefaultScopeUnlinkError,
    RoutineGraphUnresolvedError,
    RoutineNameImmutableError,
    RoutineNameTakenError,
    RunMode,
)
from blizzard.hub.domain.garden.runs.baselines import RoutineBaseline
from blizzard.hub.domain.garden.runs.run import RoutineRetiredError, RunResult, ScopeNotRelatedError, ScopeRetiredError
from blizzard.hub.domain.garden.runs.sweeps import GardenSweeps, SweepWindow
from blizzard.hub.domain.garden.runs.window import InvalidWindowError, require_until_after_since
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlug, ScopeSlugError
from blizzard.hub.domain.graph.harnesses import InvalidHarnesses
from blizzard.wire.chunk import ChunkIngestConflict
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

router = APIRouter(prefix="/api", tags=["routines"], dependencies=[Depends(reject_runner_principal)])


def _routine_view(routine: Routine, *, retired: bool) -> RoutineView:
    return RoutineView(
        routine_id=routine.routine_id,
        name=routine.name,
        graph_name=routine.graph_name,
        default_scope_slug=routine.default_scope_slug,
        default_model=list(routine.default_model),
        default_effort=routine.default_effort,
        default_harnesses=list(routine.default_harnesses),
        created_at=iso_utc(routine.created_at),
        retired=retired,
    )


@router.post(
    "/routines",
    response_model=RoutineView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def create_routine(
    request: RoutineCreateRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RoutineView:
    """Mint a routine; 422 on a duplicate name, a malformed default scope slug, or a
    graph name with no enabled mint."""
    try:
        slug = ScopeSlug.parse(request.default_scope_slug)
        routine = services.routine_authoring.create(
            name=request.name,
            graph_name=request.graph_name,
            default_scope_slug=slug,
            default_model=request.default_model,
            default_effort=request.default_effort,
            default_harnesses=request.default_harnesses,
        )
    except (ScopeSlugError, RoutineNameTakenError, RoutineGraphUnresolvedError, InvalidHarnesses) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _routine_view(routine, retired=False)


@router.get("/routines", response_model=list[RoutineView], dependencies=[Depends(require(FLEET_VIEW))])
def list_routines(
    services: Annotated[HubServices, Depends(get_services)],
    include_retired: Annotated[bool, Query()] = False,
) -> list[RoutineView]:
    """Every routine, newest first — a retired routine excluded by default, included and
    marked when ``include_retired``."""
    retired = services.routines.retired_ids()
    return [
        _routine_view(r, retired=r.routine_id in retired)
        for r in services.routines.list_all()
        if include_retired or r.routine_id not in retired
    ]


def _parse_instant(value: str, *, field: str) -> datetime:
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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


@router.get("/routines/trend", response_model=TrendView, dependencies=[Depends(require(FLEET_VIEW))])
def routine_trend(
    services: Annotated[HubServices, Depends(get_services)],
    routine: Annotated[str, Query()],
    since: Annotated[str, Query()],
    until: Annotated[str, Query()],
    introduced_boundary: Annotated[str, Query()],
    period_days: Annotated[int, Query()] = 7,
) -> TrendView:
    """`routine`'s finding inflow-against-outflow over `[since, until)`: per
    `period_days`-wide period, findings created and per-kind exit counts, the outflow/
    withdrawn roll-ups, and the age cut against `introduced_boundary`. 404 on an
    unknown routine name; 422 on a malformed instant, a non-positive `period_days`, a
    non-positive span, or a span/`period_days` pair bucketing past `TrendWindow.MAX_PERIODS`."""
    if services.routines.get_by_name(routine) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine!r}")
    parsed_since = _parse_instant(since, field="since")
    parsed_until = _parse_instant(until, field="until")
    parsed_boundary = _parse_instant(introduced_boundary, field="introduced_boundary")
    try:
        window = TrendWindow.of(
            since=parsed_since, until=parsed_until, introduced_boundary=parsed_boundary, period_days=period_days
        )
    except InvalidTrendWindow as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _trend_view(services.garden_trend.trend(routine, window))


def _proposal_counts_row_view(counts: GardenProposalCounts) -> GardenProposalCountsRowView:
    # `class_`'s alias is the Python keyword `class` — constructed by alias via
    # `model_validate`, the `garden_runs.py` `_set_delta_view` shape.
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
        }
    )


@router.get(
    "/routines/proposal-counts", response_model=GardenProposalCountsView, dependencies=[Depends(require(FLEET_VIEW))]
)
def routine_proposal_counts(
    services: Annotated[HubServices, Depends(get_services)],
    since: Annotated[str, Query()],
    until: Annotated[str, Query()],
    routine: Annotated[str | None, Query()] = None,
    origin: Annotated[GardenProposalOrigin | None, Query()] = None,
) -> GardenProposalCountsView:
    """Garden-proposal counts per origin, routine, and class over
    `[since, until)`, split into open/passed/accepted-with-item/accepted-without-item —
    `created` is their sum. `routine` narrows to one routine's rows of
    both origins when given; 404 on an unknown one. `origin` narrows to one origin. 422
    on a malformed instant or `until <= since`."""
    parsed_since = _parse_instant(since, field="since")
    parsed_until = _parse_instant(until, field="until")
    try:
        require_until_after_since(parsed_since, parsed_until)
    except InvalidWindowError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if routine is not None and services.routines.get_by_name(routine) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine!r}")
    rows = services.garden_proposals.counts_by_class(
        since=parsed_since, until=parsed_until, routine_name=routine, origin=origin
    )
    return GardenProposalCountsView(
        since=iso_utc(parsed_since),
        until=iso_utc(parsed_until),
        routine=routine,
        origin=origin,
        rows=[_proposal_counts_row_view(c) for c in rows],
    )


@router.get("/routines/{routine_id}", response_model=RoutineView, dependencies=[Depends(require(FLEET_VIEW))])
def get_routine(routine_id: str, services: Annotated[HubServices, Depends(get_services)]) -> RoutineView:
    """One routine's whole record; 404 on an unknown id."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    return _routine_view(routine, retired=services.routines.is_retired(routine_id))


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
    dependencies=[Depends(require(FLEET_VIEW))],
)
def routine_baselines(
    routine_id: str, services: Annotated[HubServices, Depends(get_services)]
) -> list[RoutineBaselineView]:
    """Every scope `routine_id` has swept — see
    `IReadFindingSetRepository.newest_by_scope_for_routine` for what absence means.
    404 on an unknown routine id."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    return [_baseline_view(b) for b in services.routine_baselines.baselines_for(routine)]


@router.get(
    "/routines/{routine_id}/scopes",
    response_model=list[str],
    dependencies=[Depends(require(FLEET_VIEW))],
)
def list_routine_scopes(routine_id: str, services: Annotated[HubServices, Depends(get_services)]) -> list[str]:
    """Every scope slug linked to `routine_id`, sorted — its own default
    scope is always among them. 404 on an unknown routine id."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    return services.routine_scopes.list_scopes(routine.routine_id)


def _resolve_scope_for_membership(scope_slug: str, services: HubServices) -> Scope:
    """Parse and resolve `scope_slug` for a link/unlink write: 422 on a malformed slug,
    404 on a well-formed but unknown one — a management verb never silently mints
    (`hub scope create` is the one deliberate mint path)."""
    try:
        slug = ScopeSlug.parse(scope_slug)
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    scope = services.scopes.get(slug.value)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown scope {scope_slug}")
    return scope


@router.put(
    "/routines/{routine_id}/scopes/{scope_slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def link_routine_scope(
    routine_id: str, scope_slug: str, services: Annotated[HubServices, Depends(get_services)]
) -> Response:
    """Link `scope_slug` into `routine_id`'s own set; idempotent. 404 on
    an unknown routine id or a well-formed but unknown scope slug; 422 on a malformed
    scope slug."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    scope = _resolve_scope_for_membership(scope_slug, services)
    services.routine_scope_membership.link(routine, scope)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/routines/{routine_id}/scopes/{scope_slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def unlink_routine_scope(
    routine_id: str, scope_slug: str, services: Annotated[HubServices, Depends(get_services)]
) -> Response:
    """Unlink `scope_slug` from `routine_id`'s own set; idempotent. 404
    on an unknown routine id or a well-formed but unknown scope slug; 422 on a malformed
    scope slug, or on naming the routine's own default scope — always a member of
    its own set."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    scope = _resolve_scope_for_membership(scope_slug, services)
    try:
        services.routine_scope_membership.unlink(routine, scope)
    except RoutineDefaultScopeUnlinkError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/routines/{routine_id}", response_model=RoutineView, dependencies=[Depends(require(GRAPH_EDIT))])
def edit_routine(
    routine_id: str, request: RoutineEditRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RoutineView:
    """Change the graph, the default scope, and the model/effort defaults; 404 on an
    unknown id, 422 on a name change, a malformed default scope slug, or a graph name
    with no enabled mint."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    try:
        slug = ScopeSlug.parse(request.default_scope_slug)
        edited = services.routine_authoring.edit(
            routine,
            name=request.name,
            graph_name=request.graph_name,
            default_scope_slug=slug,
            default_model=request.default_model,
            default_effort=request.default_effort,
            default_harnesses=request.default_harnesses,
        )
    except (ScopeSlugError, RoutineNameImmutableError, RoutineGraphUnresolvedError, InvalidHarnesses) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _routine_view(edited, retired=services.routines.is_retired(routine_id))


@router.post(
    "/routines/{routine_id}/retire",
    response_model=RoutineView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def retire_routine(
    routine_id: str, request: RoutineLifecycleRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RoutineView:
    """Retire a routine — a reversible brake; 404 on an unknown id."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    services.routine_lifecycle.retire(routine, by=request.by)
    return _routine_view(routine, retired=True)


@router.post(
    "/routines/{routine_id}/enable",
    response_model=RoutineView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def enable_routine(
    routine_id: str, request: RoutineLifecycleRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RoutineView:
    """Re-enable a retired routine; idempotent, 404 on an unknown id."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    services.routine_lifecycle.enable(routine, by=request.by)
    return _routine_view(routine, retired=False)


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
    dependencies=[Depends(require(FLEET_VIEW))],
)
def routine_sweeps(
    routine_id: str,
    services: Annotated[HubServices, Depends(get_services)],
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
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
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
        effective_mode=result.effective_mode.value,
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
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> object:
    """Mint and ingest a hub work item from the routine, in one act; its chunk rests ``not_ready``
    until promoted.
    404 on an unknown id; 422 on a malformed ``scope_slug``, an unknown
    ``mode``, or an effective scope no scope row holds or outside the routine's own
    related set (never minted); 503 on a retired routine
    (checked first, before the mode or scope is even parsed), a retired effective
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
        mode = RunMode(request.mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"unknown mode {request.mode!r}"
        ) from exc
    try:
        slug = routine.effective_scope_slug(
            ScopeSlug.parse(request.scope_slug) if request.scope_slug is not None else None
        )
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    scope = services.scopes.get(slug.value)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"unknown scope {slug.value!r}")
    try:
        result = services.routine_run.run(
            routine,
            scope=scope,
            mode=mode,
            note=request.note,
            author=WorkItemAuthor.user(identity.user_id),
        )
    except ScopeNotRelatedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"scope {exc.slug!r} is not related to routine {exc.routine_id!r} — link it first with "
                f"`blizzard hub routine scope add {exc.routine_id} {exc.slug}`"
            ),
        ) from exc
    except (RoutineGraphUnresolvedError, ScopeRetiredError, RoutineRetiredError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except IngestConflict as exc:
        conflict = ChunkIngestConflict(
            existing_chunk_id=exc.existing_chunk_id, source=exc.pointer.source, ref=exc.pointer.ref
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    # The chunk is minted `not_ready`, so its post-write status reads `not_ready`.
    chunk_events.ChunkChanged.of(services, result.chunk_id, prev_status=None).publish(
        cause="minted", key=f"chunks:{result.chunk_id}"
    )
    services.events.publish_queue_changed()  # the mint adds the chunk to the backlog list
    return _run_response(result)
