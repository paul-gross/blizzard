"""``blizzard runner chunk history`` — a worker's read of its own chunk's transition
history (issue #237).

Lease-scoped and token-authorized, then forwarded to the hub as the runner principal.
``503`` unwired, ``404`` unknown/closed lease, ``403`` bad token, ``502`` on a failed
forward; authorization resolves before the hub is consulted."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.history import ChunkHistoryView, HistoryRowView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/history", response_model=list[HistoryRowView])
def get_history(lease_id: str, request: Request) -> list[HistoryRowView]:
    """The worker's own chunk's timeline — transitions, migrations, and bounces merged
    oldest-first into one kind-discriminated read. Does not include the in-flight
    node-step this call is itself part of: a transition is recorded only once an
    attempt completes, so a worker must not read its own current step's absence as a
    gap in the history."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "history").get(f"/api/fleet/chunks/{lease.chunk_id}", chunk_id=lease.chunk_id)
    return ChunkHistoryView.model_validate(upstream.json()).rows()
