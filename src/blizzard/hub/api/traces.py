"""Fleet-trace operator-plane routes: the status read. Never ``/api/fleet/...``; a runner
principal is refused. Status gates on :data:`~blizzard.auth_core.FLEET_VIEW` — it is redacted, so it
holds nothing beyond fleet state."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from blizzard.auth_core import FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.wire.traces import TraceStatusResponse

router = APIRouter(prefix="/api/traces", tags=["traces"], dependencies=[Depends(reject_runner_principal)])


@router.get("/status", response_model=TraceStatusResponse, dependencies=[Depends(require(FLEET_VIEW))])
def trace_status(services: Annotated[HubServices, Depends(get_services)]) -> TraceStatusResponse:
    """Tracing on or off, the redacted endpoint, the cursor and its lag, the last export and the last error."""
    status = services.trace_status.read()
    return TraceStatusResponse(
        state=status.state,
        endpoint=status.endpoint,
        rejected_setting=status.rejected_setting,
        rejected_value=status.rejected_value,
        cursor_at=iso_utc(status.cursor_at) if status.cursor_at else None,
        lag_seconds=status.lag_seconds,
        last_export_at=iso_utc(status.last_export_at) if status.last_export_at else None,
        last_export_span_count=status.last_export_span_count,
        last_error_at=iso_utc(status.last_error_at) if status.last_error_at else None,
        last_error_message=status.last_error_message,
        last_error_ongoing=status.last_error_ongoing,
    )
