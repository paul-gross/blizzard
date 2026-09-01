"""Routine routes — create, list, read, edit, run, trend, and sweep (issue #389, blizzard#392).

The controller stays read-only (``bzh:controller-read-only``), resolving a ``routine_id``
before delegating to the domain. ``GET /routines/trend`` is declared ahead of ``GET
/routines/{routine_id}`` so the literal path wins; ``sweeps`` nests under a resolved id (D6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import CHUNK_CONTROL, FLEET_VIEW, GRAPH_EDIT
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.garden_sweeps import GardenSweeps
from blizzard.hub.domain.garden_trend import Trend
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.hub.domain.routine_run import RunResult, ScopeRetiredError
from blizzard.hub.domain.routines import (
    Routine,
    RoutineGraphUnresolvedError,
    RoutineNameImmutableError,
    RoutineNameTakenError,
    RunMode,
)
from blizzard.hub.domain.scopes import ScopeSlug, ScopeSlugError
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.wire.chunk import ChunkIngestConflict
from blizzard.wire.garden_sweeps import GardenSweepsView, MeasurementReadingView, ScopeSweepView
from blizzard.wire.garden_trend import TrendAgeView, TrendPeriodView, TrendView
from blizzard.wire.routine import (
    RoutineCreateRequest,
    RoutineEditRequest,
    RoutineRunRequest,
    RoutineRunResponse,
    RoutineView,
)

router = APIRouter(prefix="/api", tags=["routines"], dependencies=[Depends(reject_runner_principal)])


def _routine_view(routine: Routine) -> RoutineView:
    return RoutineView(
        routine_id=routine.routine_id,
        name=routine.name,
        graph_name=routine.graph_name,
        default_scope_slug=routine.default_scope_slug,
        default_model=list(routine.default_model),
        default_effort=routine.default_effort,
        created_at=iso_utc(routine.created_at),
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
        )
    except (ScopeSlugError, RoutineNameTakenError, RoutineGraphUnresolvedError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _routine_view(routine)


@router.get("/routines", response_model=list[RoutineView], dependencies=[Depends(require(FLEET_VIEW))])
def list_routines(services: Annotated[HubServices, Depends(get_services)]) -> list[RoutineView]:
    """Every routine, newest first."""
    return [_routine_view(r) for r in services.routines.list_all()]


def _parse_instant(value: str, *, field: str) -> datetime:
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} {value!r} is not a valid ISO-8601 instant",
        ) from exc


def _require_until_after_since(since: datetime, until: datetime) -> None:
    if until <= since:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="until must be after since")


@dataclass(frozen=True)
class _TrendWindow:
    """One ``GET /routines/trend`` request's parsed window (blizzard#394 Phase 4,
    `SpendWindow`'s own shape, `src/blizzard/hub/api/spend.py`) — a malformed edge or a
    non-positive ``period_days`` is the 422 it names."""

    since: datetime
    until: datetime
    introduced_boundary: datetime
    period_days: int

    #: The span/`period_days` bucket cap — otherwise unbounded (blizzard#394).
    _MAX_PERIODS = 366

    @classmethod
    def of(cls, *, since: str, until: str, introduced_boundary: str, period_days: int) -> _TrendWindow:
        if period_days < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="period_days must be at least 1"
            )
        parsed_since = _parse_instant(since, field="since")
        parsed_until = _parse_instant(until, field="until")
        _require_until_after_since(parsed_since, parsed_until)
        span_days = (parsed_until - parsed_since).total_seconds() / 86400
        if span_days / period_days > cls._MAX_PERIODS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"since/until/period_days would bucket more than {cls._MAX_PERIODS} periods",
            )
        return cls(
            since=parsed_since,
            until=parsed_until,
            introduced_boundary=_parse_instant(introduced_boundary, field="introduced_boundary"),
            period_days=period_days,
        )


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
    withdrawn roll-ups (D2), and the D5 age cut against `introduced_boundary`. 404 on an
    unknown routine name; 422 on a malformed instant, a non-positive `period_days`, a
    non-positive span, or a span/`period_days` pair bucketing past `_TrendWindow._MAX_PERIODS`."""
    if services.routines.get_by_name(routine) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine!r}")
    window = _TrendWindow.of(since=since, until=until, introduced_boundary=introduced_boundary, period_days=period_days)
    trend = services.garden_trend.trend(
        routine,
        since=window.since,
        until=window.until,
        period_days=window.period_days,
        introduced_boundary=window.introduced_boundary,
    )
    return _trend_view(trend)


@router.get("/routines/{routine_id}", response_model=RoutineView, dependencies=[Depends(require(FLEET_VIEW))])
def get_routine(routine_id: str, services: Annotated[HubServices, Depends(get_services)]) -> RoutineView:
    """One routine's whole record; 404 on an unknown id."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    return _routine_view(routine)


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
        )
    except (ScopeSlugError, RoutineNameImmutableError, RoutineGraphUnresolvedError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _routine_view(edited)


@dataclass(frozen=True)
class _SweepWindow:
    """One ``GET /routines/{routine_id}/sweeps`` request's parsed window — the
    measurement series' own ``[since, until)`` (D2); last-swept ignores it. Reuses
    `_parse_instant`/`_require_until_after_since` so a malformed instant or an
    inverted span answers the same 422 both routes name."""

    since: datetime
    until: datetime

    @classmethod
    def of(cls, *, since: str, until: str) -> _SweepWindow:
        parsed_since = _parse_instant(since, field="since")
        parsed_until = _parse_instant(until, field="until")
        _require_until_after_since(parsed_since, parsed_until)
        return cls(since=parsed_since, until=parsed_until)


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
    """``routine_id``'s per-scope last-swept table (D2, D3, D4) — every non-retired
    scope, plus any retired scope this routine has swept — and its measurement series
    (D2, D5) over ``[since, until)``. 404 on an unknown id; 422 on a malformed instant
    or a non-positive span."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    window = _SweepWindow.of(since=since, until=until)
    sweeps = services.garden_sweeps.sweeps(routine.name, since=window.since, until=window.until)
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
    """Mint, ingest, and promote a hub work item from the routine, in one act
    (blizzard#392). 404 on an unknown id; 422 on a malformed ``scope_slug`` or an unknown
    ``mode``; 503 on a retired effective scope or a graph name with no enabled mint (D5,
    mirroring ``POST /work-sources/{source}/items``'s own retired-default-graph shape);
    409 on an out-of-band ingest already holding the allocated ref's pointer."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    try:
        mode = RunMode(request.mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"unknown mode {request.mode!r}"
        ) from exc
    try:
        slug = ScopeSlug.parse(request.scope_slug) if request.scope_slug is not None else None
        result = services.routine_run.run(
            routine,
            scope_slug=slug,
            mode=mode,
            note=request.note,
            author=WorkItemAuthor.user(identity.user_id),
        )
    except ScopeSlugError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except (RoutineGraphUnresolvedError, ScopeRetiredError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except IngestConflict as exc:
        conflict = ChunkIngestConflict(
            existing_chunk_id=exc.existing_chunk_id, source=exc.pointer.source, ref=exc.pointer.ref
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    # A freshly minted chunk is promoted in the same transaction, so its post-write
    # status already reads `ready` — one frame, not a mint then a separate promote.
    chunk_events.ChunkChanged.of(services, result.chunk_id, prev_status=None).publish(
        cause="minted", key=f"chunks:{result.chunk_id}"
    )
    services.events.publish_queue_changed()  # a promoted chunk enters the ready queue
    return _run_response(result)
