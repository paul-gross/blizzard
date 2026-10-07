"""Fact-egress operator-plane routes. Never ``/api/fleet/...``; a runner principal is refused. Status gates on
:attr:`~blizzard.auth_core.Permission.FLEET_VIEW` — it holds export state and a directory path, nothing a fleet viewer
cannot already see — and the writes on :attr:`~blizzard.auth_core.Permission.ANALYTICS_ADMIN`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.observability.egress.backfill import BackfillUnavailable, BackfillWindowRefused
from blizzard.hub.domain.observability.egress.reset import ResetRefused, ResetUnavailable
from blizzard.wire.egress import (
    EgressBackfillCount,
    EgressBackfillFailure,
    EgressBackfillRequest,
    EgressBackfillResponse,
    EgressDatasetStatus,
    EgressResetRequest,
    EgressResetResponse,
    EgressStatusResponse,
)

router = APIRouter(prefix="/api/egress", tags=["egress"], dependencies=[Depends(reject_runner_principal)])


@router.get("/status", response_model=EgressStatusResponse, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def egress_status(services: Annotated[HubServices, Depends(get_services)]) -> EgressStatusResponse:
    """Export on, off or rejected, each dataset's cursor and lag, the last pass and file, the last error and the
    free space."""
    read = services.egress_status.read()
    return EgressStatusResponse(
        state=read.state,
        rejected_setting=read.rejected_setting,
        rejected_value=read.rejected_value,
        directory=read.directory,
        format=read.format,
        datasets=[
            EgressDatasetStatus(
                name=d.name, cursor_at=iso_utc(d.cursor_at) if d.cursor_at else None, lag_seconds=d.lag_seconds
            )
            for d in read.datasets
        ],
        last_pass_at=iso_utc(read.last_pass_at) if read.last_pass_at else None,
        last_pass_dataset=read.last_pass_dataset,
        last_pass_row_count=read.last_pass_row_count,
        last_file=read.last_file,
        last_error_at=iso_utc(read.last_error_at) if read.last_error_at else None,
        last_error_message=read.last_error_message,
        last_error_ongoing=read.last_error_ongoing,
        free_bytes=read.free_bytes,
        min_free_bytes=read.min_free_bytes,
        backfill_max_window_seconds=read.backfill_max_window_seconds,
    )


@router.post("/reset", response_model=EgressResetResponse, dependencies=[Depends(require(Permission.ANALYTICS_ADMIN))])
def egress_reset(
    request: EgressResetRequest, services: Annotated[HubServices, Depends(get_services)]
) -> EgressResetResponse:
    """Move one dataset's cursor to an instant and record the moved window. An unconfigured dataset or a future
    instant is 422, and a reset while the export is off or rejected is 409."""
    try:
        result = services.egress_reset.reset(request.dataset, as_utc(request.to))
    except ResetRefused as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ResetUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return EgressResetResponse(
        dataset=result.dataset,
        from_at=iso_utc(result.previous) if result.previous else None,
        to_at=iso_utc(result.moved_to),
        direction=result.direction,
    )


@router.post(
    "/backfill",
    response_model=EgressBackfillResponse,
    responses={status.HTTP_502_BAD_GATEWAY: {"model": EgressBackfillFailure}},
    dependencies=[Depends(require(Permission.ANALYTICS_ADMIN))],
)
def egress_backfill(
    request: EgressBackfillRequest, services: Annotated[HubServices, Depends(get_services)]
) -> EgressBackfillResponse | JSONResponse:
    """Write the rows of ``[since, until)`` again as the live export would, without moving a cursor. A bad window
    — inverted, too wide, or reaching past now — or dataset is 422, a wet backfill while the export is off or
    rejected is 409 (a dry run only counts, so it runs), and a writer that refuses is 502 with the counts
    committed before."""
    try:
        result = services.egress_backfill.backfill(
            as_utc(request.since), as_utc(request.until), dataset=request.dataset, dry_run=request.dry_run
        )
    except BackfillWindowRefused as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except BackfillUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    counts = [EgressBackfillCount(dataset=c.dataset, rows=c.rows, files=c.files) for c in result.datasets]
    if result.failure is not None:
        failure = EgressBackfillFailure(
            detail="the egress writer did not accept a batch; the backfill stopped",
            cause=result.failure.cause.value,
            datasets=counts,
        )
        return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content=failure.model_dump())
    return EgressBackfillResponse(dry_run=result.dry_run, datasets=counts)
