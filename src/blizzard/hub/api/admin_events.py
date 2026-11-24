"""The hub's own event log, read by hub administrators alone — hub-level, under ``TENANT_ADMIN``. Its occurrences
concern no tenant, so no tenant member, a tenant's admins included, reads them."""

from __future__ import annotations

from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Query

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.domain.observability.hub_events import IReadHubEventLog
from blizzard.wire.tenant import HubEventView

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(reject_runner_principal)])

#: The most rows one read answers.
_MAX_EVENTS = 500


@router.get("/events", response_model=list[HubEventView], dependencies=[Depends(require_hub(Permission.TENANT_ADMIN))])
@inject_sync
def hub_events(
    log: FromDishka[IReadHubEventLog], limit: Annotated[int, Query(ge=1, le=_MAX_EVENTS)] = 100
) -> list[HubEventView]:
    """The newest ``limit`` rows of the hub's own event log, newest first."""
    return [
        HubEventView(
            id=e.event_id,
            severity=e.severity.value,
            kind=e.kind,
            recorded_at=iso_utc(e.at),
            message=e.message,
            detail=e.detail,
        )
        for e in log.recent(limit)
    ]
