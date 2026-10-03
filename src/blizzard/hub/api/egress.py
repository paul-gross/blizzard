"""Fact-egress operator-plane routes. Never ``/api/fleet/...``; a runner principal is refused. Status gates on
:data:`~blizzard.auth_core.FLEET_VIEW` — it holds export state and a directory path, nothing a fleet viewer
cannot already see — and the writes on :data:`~blizzard.auth_core.ANALYTICS_ADMIN`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from blizzard.auth_core import FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.wire.egress import EgressDatasetStatus, EgressStatusResponse

router = APIRouter(prefix="/api/egress", tags=["egress"], dependencies=[Depends(reject_runner_principal)])


@router.get("/status", response_model=EgressStatusResponse, dependencies=[Depends(require(FLEET_VIEW))])
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
