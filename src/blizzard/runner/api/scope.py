"""``GET /api/leases/{lease_id}/scopes`` — the deployment's scope vocabulary, lease-token-authorized
and forwarded to the hub as the runner principal, narrowed to the lease's chunk's project."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.scope import ScopeView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/scopes", response_model=list[ScopeView])
def list_scopes(lease_id: str, request: Request) -> list[ScopeView]:
    """Forward this lease's scope-list read to the hub — the layered pass-through. The
    lease authorizes the caller and names its chunk, so the hub answers that chunk's project's
    scopes; a hub that predates projects ignores the chunk and answers every scope."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "scope").get(
        "/api/fleet/scopes", lease_id=lease_id, params={"chunk_id": lease.chunk_id}
    )
    return [ScopeView.model_validate(item) for item in upstream.json()]
