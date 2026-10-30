"""The runner-local lease list — ``GET /api/leases``.

Active leases plus recently-closed ones, answered entirely from the local sqlite store and
the process probe. **Hub-free** by design: no hub call, no forge call, no title, and
read-only over its wiring (``bzh:controller-read-only``)."""

from __future__ import annotations

from typing import TypedDict

from fastapi import APIRouter, Request

from blizzard.foundation.leases import LeaseDisplayStatus
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.leases.activity import HEARTBEAT_STALENESS_THRESHOLD, ClosedLeaseActivity, LeaseActivity
from blizzard.wire.git_commits import LeaseGitCommitView
from blizzard.wire.lease import LeaseListResponse, LeaseView

router = APIRouter(prefix="/api", tags=["runner"])


class _CommonFields(TypedDict):
    """The `LeaseView` fields both the active and the closed branch fill identically."""

    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    session_id: str | None
    harness_id: str | None
    pid: int | None
    created_at: str
    display_status: LeaseDisplayStatus
    project_id: str | None
    stale_after_seconds: int
    resolved_model: str | None
    resolved_effort: str | None
    harness_version: str | None
    git_commits: list[LeaseGitCommitView]


def _view(activity: LeaseActivity | ClosedLeaseActivity) -> LeaseView:
    lease = activity.lease
    common: _CommonFields = {
        "lease_id": lease.lease_id,
        "chunk_id": lease.chunk_id,
        "graph_id": lease.graph_id,
        "node_id": lease.node_id,
        "node_name": lease.node_name,
        "epoch": lease.epoch,
        "session_id": lease.session_id,
        "harness_id": lease.harness_id,
        "pid": lease.pid,
        "created_at": iso_utc(lease.created_at),
        "display_status": activity.display_status,
        "project_id": activity.project_id,
        "stale_after_seconds": int(HEARTBEAT_STALENESS_THRESHOLD.total_seconds()),
        "resolved_model": lease.resolved_model,
        "resolved_effort": lease.resolved_effort,
        "harness_version": activity.stamps.harness_version,
        "git_commits": [
            LeaseGitCommitView(
                environment_id=commit.environment_id,
                repo=commit.repo,
                branch=commit.branch,
                commit=commit.commit,
                declared_at=iso_utc(commit.declared_at),
            )
            for commit in activity.stamps.git_commits
        ],
    }
    if isinstance(activity, ClosedLeaseActivity):
        return LeaseView(
            **common,
            environment_id=None,
            workdir=None,
            last_heartbeat_at=None,
            state=activity.state,
            closure_healthy=activity.closure_healthy,
            closed_at=iso_utc(activity.closed_at),
            closure_reason=activity.closure_reason,
        )
    return LeaseView(
        **common,
        environment_id=activity.environment_id,
        workdir=activity.workdir,
        last_heartbeat_at=iso_utc(activity.last_heartbeat_at) if activity.last_heartbeat_at is not None else None,
        state=activity.state,
        closure_healthy=False,
        closed_at=None,
        closure_reason=None,
    )


@router.get("/leases", response_model=LeaseListResponse)
def list_leases(request: Request, chunk_id: str | None = None) -> LeaseListResponse:
    """Active leases, then recently-closed ones, derived at read time.

    With ``chunk_id``, every lease of that chunk instead — open and closed, oldest mint first,
    uncapped: one chunk's node-steps and retries bound it."""
    service = RunnerWiring.of(request).leases()
    activities = service.list_recent() if chunk_id is None else service.list_for_chunk(chunk_id)
    return LeaseListResponse(items=[_view(activity) for activity in activities])
