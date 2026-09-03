"""``GET /api/leases/{lease_id}/garden/findings`` — a worker's own routine's live finding
bucket (D4, D5). Lease-scoped and token-authorized, then forwarded to the hub as the
runner principal — the shape ``runner/api/history.py`` already sets for a lease-token-
authorized, hub-proxied node-scope read (``bzh:pluggable-seams``)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.finding import FindingView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/garden/findings", response_model=list[FindingView])
def list_garden_findings(lease_id: str, request: Request) -> list[FindingView]:
    """Forward this lease's chunk's garden-findings read to the hub — the layered
    pass-through. A chunk with no run context (not a routine run) reaches this only as
    the hub's own refusal, forwarded verbatim rather than answered as an empty bucket."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "garden").get(
        f"/api/fleet/chunks/{lease.chunk_id}/garden/findings", chunk_id=lease.chunk_id
    )
    return [FindingView.model_validate(item) for item in upstream.json()]
