"""``GET /api/leases/{lease_id}/scopes`` — the deployment's scope vocabulary (blizzard#582
D2), lease-scoped and token-authorized, then forwarded to the hub as the runner
principal — the shape ``runner/api/garden.py`` already sets for a lease-token-authorized,
hub-proxied read (``bzh:pluggable-seams``)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.scope import ScopeView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/scopes", response_model=list[ScopeView])
def list_scopes(lease_id: str, request: Request) -> list[ScopeView]:
    """Forward this lease's scope-list read to the hub — the layered pass-through. The
    lease authorizes the caller; the read itself names no chunk, since a scope is global."""
    authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "scope").get("/api/fleet/scopes", lease_id=lease_id)
    return [ScopeView.model_validate(item) for item in upstream.json()]
