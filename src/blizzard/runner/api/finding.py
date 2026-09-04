"""``GET /api/leases/{lease_id}/findings`` and its ``/{finding_id}`` sibling — the
findings a worker's own chunk's accepted, minted garden proposal answers (blizzard#397
Phase 2). Lease-scoped and token-authorized, then forwarded to the hub as the runner
principal — the shape ``runner/api/garden.py`` already sets for a lease-token-authorized,
hub-proxied read (``bzh:pluggable-seams``)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.finding import FindingView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/findings", response_model=list[FindingView])
def list_findings(lease_id: str, request: Request) -> list[FindingView]:
    """Forward this lease's chunk's own answered-findings read to the hub — the layered
    pass-through. A chunk answering no accepted, minted garden proposal reaches this only
    as the hub's own refusal, forwarded verbatim rather than answered as an empty list."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "finding").get(
        f"/api/fleet/chunks/{lease.chunk_id}/findings", chunk_id=lease.chunk_id
    )
    return [FindingView.model_validate(item) for item in upstream.json()]


@router.get("/leases/{lease_id}/findings/{finding_id}", response_model=FindingView)
def get_finding(lease_id: str, finding_id: str, request: Request) -> FindingView:
    """One finding within this lease's chunk's own answered set — forwarded the same
    way; an id outside that set (or a chunk answering no such proposal at all) is the
    hub's own refusal, forwarded verbatim."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "finding").get(
        f"/api/fleet/chunks/{lease.chunk_id}/findings/{finding_id}", chunk_id=lease.chunk_id, finding_id=finding_id
    )
    return FindingView.model_validate(upstream.json())
