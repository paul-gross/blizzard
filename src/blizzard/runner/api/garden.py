"""``GET /api/leases/{lease_id}/garden/findings`` and ``.../garden/proposals`` — a
worker's own finding bucket
and open garden-proposal docket. Lease-scoped and token-authorized, then forwarded to
the hub as the runner principal (``bzh:pluggable-seams``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.finding import FindingView
from blizzard.wire.garden_proposal import GardenProposalView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/garden/findings", response_model=list[FindingView])
def list_garden_findings(lease_id: str, request: Request) -> list[FindingView]:
    """Forward this lease's chunk's finding-bucket read to the hub; its refusals pass through verbatim."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "garden").get(
        f"/api/fleet/chunks/{lease.chunk_id}/garden/findings", chunk_id=lease.chunk_id
    )
    return [FindingView.model_validate(item) for item in upstream.json()]


@router.get("/leases/{lease_id}/garden/proposals", response_model=list[GardenProposalView])
def list_garden_proposals(
    lease_id: str, request: Request, state: Annotated[str, Query()] = "open"
) -> list[GardenProposalView]:
    """Forward this lease's chunk's garden-proposals read to the hub, ``state`` unvalidated;
    its refusals pass through verbatim."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "garden").get(
        f"/api/fleet/chunks/{lease.chunk_id}/garden/proposals", params={"state": state}, chunk_id=lease.chunk_id
    )
    return [GardenProposalView.model_validate(item) for item in upstream.json()]
