"""The runner-local active-lease list — ``GET /api/leases`` (issue #28).

The panel's backing route: an active lease *is* an active agent, so this is
"which agents are working, on what, on which node, and is that node healthy" —
answered entirely from the local sqlite store and the process probe. **Hub-free**
by design (design/runner/web-app.md): the machine panel is precisely the part of
the app that must not depend on the hub, so this route gains no hub call, no forge
call, no title — those arrive separately, by a strictly severable read.

Read-only over its wiring (``bzh:controller-read-only``): the edge holds only the
composition-root-wired :class:`LocalLeaseService`, no repository at all — it maps
domain :class:`LeaseActivity` to :class:`LeaseView` (the ``_view`` precedent is
``hub/api/runners.py``). On the store-free app (OpenAPI export / unit tests) the
service is unwired and the probe answers 503 rather than pretending.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.leases import LeaseActivity, LocalLeaseService
from blizzard.wire.lease import LeaseListResponse, LeaseView

router = APIRouter(prefix="/api", tags=["runner"])


def _view(activity: LeaseActivity) -> LeaseView:
    lease = activity.lease
    return LeaseView(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        graph_id=lease.graph_id,
        node_id=lease.node_id,
        node_name=lease.node_name,
        epoch=lease.epoch,
        session_id=lease.session_id,
        pid=lease.pid,
        environment_id=activity.environment_id,
        workdir=activity.workdir,
        created_at=iso_utc(lease.created_at),
        last_heartbeat_at=iso_utc(activity.last_heartbeat_at) if activity.last_heartbeat_at is not None else None,
        state=activity.state,
    )


@router.get("/leases", response_model=LeaseListResponse)
def list_leases(request: Request) -> LeaseListResponse:
    """Every active lease, derived at read time (issue #28) — local store only."""
    service: LocalLeaseService | None = getattr(request.app.state, "leases", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="lease service not wired — start via `blizzard runner host`",
        )
    return LeaseListResponse(items=[_view(activity) for activity in service.list_active()])
