"""Flow routes — each branch's delivery line as the board reads it: the tenant's flows, one flow, and a flow's record.

Every route reads; nothing here creates, edits, holds, or reorders a flow. The flows are the tenant's, not a
project's: a branch's line is shared by every project landing on it."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.auth_core import Permission
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.board_window import BOARD_DONE_WINDOW
from blizzard.hub.api.deps import Services
from blizzard.hub.api.flow_places import FlowLineRead, fact_view
from blizzard.hub.domain.delivery.flows import FlowOverview
from blizzard.wire.flow import FlowListView, FlowRecordPage, FlowView

#: The most facts one record page carries.
MAX_RECORD_LIMIT = 200

router = APIRouter(
    prefix="/api", tags=["flows"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)

_VIEW = Depends(require(Permission.FLEET_VIEW))


def _overview(flow_id: str, services: Services) -> FlowOverview:
    """The flow ``{flow_id}`` names; ``404`` when the tenant holds none by that id."""
    found = services.flow_views.flow_overview(flow_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown flow {flow_id}")
    return found


PathFlow = Annotated[FlowOverview, Depends(_overview)]


@router.get("/flows", response_model=FlowListView, dependencies=[_VIEW])
def list_flows(services: Services, active: Annotated[bool, Query()] = False) -> FlowListView:
    """The tenant's flows in resource order, each with its open turn, its line, and the newest of its record. With
    ``active``, only those with an open submission or a fact in the last 48 hours."""
    overviews = services.flow_views.flow_overviews()
    read = FlowLineRead.of(services, services.flow_views.open_submissions_on([o.flow.flow_id for o in overviews]))
    since = read.now - BOARD_DONE_WINDOW
    return FlowListView(
        flows=[
            read.flow_view(overview)
            for overview in overviews
            if not active or overview.is_active(in_line=read.in_line(overview.flow.flow_id), since=since)
        ]
    )


@router.get("/flows/{flow_id}", response_model=FlowView, dependencies=[_VIEW])
def get_flow(overview: PathFlow, services: Services) -> FlowView:
    """One flow with its open turn, its line, and the newest of its record: 200, or 404 for one the tenant does not
    hold."""
    return FlowLineRead.around(services, [overview.flow.flow_id]).flow_view(overview)


@router.get("/flows/{flow_id}/record", response_model=FlowRecordPage, dependencies=[_VIEW])
def get_flow_record(
    overview: PathFlow,
    services: Services,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_RECORD_LIMIT)] = 50,
) -> FlowRecordPage:
    """The flow's record newest first, keyset-paged on ``id``: pass the page's ``next_before`` as ``before`` for the
    next. 404 for a flow the tenant does not hold."""
    rows = services.flow_views.record_page(overview.flow.flow_id, before=before, limit=limit + 1)
    page = rows[:limit]
    return FlowRecordPage(
        facts=[fact_view(fact) for fact in page], next_before=page[-1].id if len(rows) > limit else None
    )
