"""Runner routes — the fleet registry (D-019/D-070/D-043).

The fleet-registry surface (design/hub/api.md): ``POST /runners`` registers a runner and
makes it visible on the board; ``GET /runners`` lists the fleet with derived liveness;
``GET /runners/{id}`` is the runner's own pull read of its declarative state;
``POST /runners/{id}/heartbeats`` refreshes liveness (D-070); and
``POST /runners/{id}/pause`` / ``/resume`` set the operator's pause brake (D-043) — the
runner reads ``paused`` back on its pull and adheres (no new claims; in-flight runs on).

Controllers stay read-only over the store and delegate the writes to
:class:`~blizzard.hub.domain.registry.FleetService` (``bzh:controller-read-only``). A
registry state change re-broadcasts ``runner-changed`` so the board's fleet column
live-updates.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.registry import RunnerLiveness
from blizzard.wire.runner import (
    RunnerListResponse,
    RunnerPauseRequest,
    RunnerRegistrationRequest,
    RunnerRegistrationResponse,
    RunnerView,
)

router = APIRouter(prefix="/api", tags=["runners"])


def _view(liveness: RunnerLiveness) -> RunnerView:
    r = liveness.registration
    return RunnerView(
        runner_id=r.runner_id,
        workspace_id=r.workspace_id,
        registered_at=r.registered_at.isoformat(),
        last_seen_at=r.last_seen_at.isoformat(),
        online=liveness.online,
        paused=r.paused,
    )


@router.post("/runners", response_model=RunnerRegistrationResponse, status_code=status.HTTP_201_CREATED)
def register_runner(
    request: RunnerRegistrationRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerRegistrationResponse:
    """Register a runner — runner id + workspace binding (D-019); idempotent upsert."""
    first = services.fleet.register(request.runner_id, request.workspace_id)
    services.events.publish_runner_changed(request.runner_id)
    return RunnerRegistrationResponse(runner_id=request.runner_id, first_registration=first)


@router.get("/runners", response_model=RunnerListResponse)
def list_runners(services: Annotated[HubServices, Depends(get_services)]) -> RunnerListResponse:
    """The fleet registry — every runner with derived liveness + paused state (D-070)."""
    return RunnerListResponse(runners=[_view(item) for item in services.fleet.list_with_liveness()])


@router.get("/runners/{runner_id}", response_model=RunnerView)
def get_runner(runner_id: str, services: Annotated[HubServices, Depends(get_services)]) -> RunnerView:
    """One runner's declarative state — the runner's own pull read (D-043/D-070)."""
    liveness = services.fleet.get_liveness(runner_id)
    if liveness is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    return _view(liveness)


@router.post("/runners/{runner_id}/heartbeats", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat_runner(runner_id: str, services: Annotated[HubServices, Depends(get_services)]) -> Response:
    """Refresh a runner's liveness (D-070) — the slow runner-level heartbeat. Returns 204."""
    if not services.fleet.heartbeat(runner_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    services.events.publish_runner_changed(runner_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/runners/{runner_id}/pause", response_model=RunnerView)
def pause_runner(
    runner_id: str, request: RunnerPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerView:
    """Set a runner's pause brake — no new claims; in-flight chunks run on (D-043)."""
    return _set_paused(runner_id, paused=True, by=request.by, services=services)


@router.post("/runners/{runner_id}/resume", response_model=RunnerView)
def resume_runner(
    runner_id: str, request: RunnerPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerView:
    """Clear a runner's pause brake — it resumes claiming on its next pull (D-043)."""
    return _set_paused(runner_id, paused=False, by=request.by, services=services)


def _set_paused(runner_id: str, *, paused: bool, by: str, services: HubServices) -> RunnerView:
    if not services.fleet.set_paused(runner_id, paused=paused, by=by):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    services.events.publish_runner_changed(runner_id)
    liveness = services.fleet.get_liveness(runner_id)
    assert liveness is not None  # just set_paused succeeded, so the runner exists
    return _view(liveness)
