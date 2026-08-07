"""The runner-local lease list — ``GET /api/leases`` (issue #28; widened issue #29).

Active leases plus recently-closed ones, answered entirely from the local sqlite store and
the process probe. **Hub-free** by design: no hub call, no forge call, no title, and
read-only over its wiring (``bzh:controller-read-only``)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.leases import LeaseActivity
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
        closed_at=iso_utc(activity.closed_at) if activity.closed_at is not None else None,
        closure_reason=activity.closure_reason,
    )


@router.get("/leases", response_model=LeaseListResponse)
def list_leases(request: Request) -> LeaseListResponse:
    """Active leases, then recently-closed ones, derived at read time (issue #28/#29)."""
    service = RunnerWiring.of(request).leases()
    return LeaseListResponse(items=[_view(activity) for activity in service.list_recent()])
