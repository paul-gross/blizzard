"""Runner routes — the anonymous **operator** half of the fleet registry (issue #87).

``GET /runners`` lists the fleet with derived liveness for the board's fleet column;
``POST /runners/{id}/pause`` / ``/resume`` set the operator's pause brake — the runner
reads ``paused`` back on its pull and adheres (no new claims; in-flight runs on).
``POST /runners/{id}/enrollments`` (issue #86a) mints/rotates the runner's bearer
token — an anonymous **operator** verb under this epic (localhost dogfooding hub;
operator auth is epic:team), distinct from the runner-auth check applied to
registration.

Registration (``POST /runners``), the runner's own pull read (``GET /runners/{id}``),
and the heartbeat (``POST /runners/{id}/heartbeats``) moved to the runner-authenticated
fleet router (:mod:`blizzard.hub.api.fleet`, issue #87) — no board or CLI caller ever
reached them. :func:`runner_view` stays here, public, so the fleet router's own
``get_runner`` reuses this module's rendering rather than duplicating it.

Controllers stay read-only over the store and delegate the writes to
:class:`~blizzard.hub.domain.registry.FleetService` (``bzh:controller-read-only``). A
registry state change re-broadcasts ``runner-changed`` so the board's fleet column
live-updates. ``dependencies=[Depends(reject_runner_principal)]`` rejects a runner's
bearer token on this router rather than treating it as anonymous-plus-credential — a
runner's token is confined to the fleet router.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.registry import RunnerLiveness
from blizzard.wire.runner import (
    RunnerEnrollmentResponse,
    RunnerListResponse,
    RunnerPauseRequest,
    RunnerView,
)

router = APIRouter(prefix="/api", tags=["runners"], dependencies=[Depends(reject_runner_principal)])


def runner_view(liveness: RunnerLiveness) -> RunnerView:
    r = liveness.registration
    return RunnerView(
        runner_id=r.runner_id,
        workspace_id=r.workspace_id,
        registered_at=iso_utc(r.registered_at),
        last_seen_at=iso_utc(r.last_seen_at),
        online=liveness.online,
        hub_paused=r.hub_paused,
        locally_paused=r.locally_paused,
        locally_paused_by=r.locally_paused_by,
        locally_paused_reason=r.locally_paused_reason,
    )


@router.post(
    "/runners/{runner_id}/enrollments",
    response_model=RunnerEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def enroll_runner(runner_id: str, services: Annotated[HubServices, Depends(get_services)]) -> RunnerEnrollmentResponse:
    """Mint (or rotate) ``runner_id``'s bearer token — the plaintext is returned once;
    the store keeps only its sha256 hash from here on (issue #86a).

    Anonymous, like every operator verb under this epic (localhost dogfooding hub;
    operator auth is epic:team). Requires an existing registration (404 otherwise):
    enrollment is a deliberate operator act on a runner the fleet already knows, not a
    trust-on-first-use grant to a name nobody has registered yet."""
    liveness = services.fleet.get_liveness(runner_id)
    if liveness is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    token = services.enrollment.enroll(liveness.registration)
    return RunnerEnrollmentResponse(runner_id=runner_id, token=token)


@router.get("/runners", response_model=RunnerListResponse)
def list_runners(services: Annotated[HubServices, Depends(get_services)]) -> RunnerListResponse:
    """The fleet registry — every runner with derived liveness + paused state."""
    return RunnerListResponse(runners=[runner_view(item) for item in services.fleet.list_with_liveness()])


@router.post("/runners/{runner_id}/pause", response_model=RunnerView)
def pause_runner(
    runner_id: str, request: RunnerPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerView:
    """Set a runner's pause brake — no new claims; in-flight chunks run on."""
    return _set_paused(runner_id, paused=True, by=request.by, services=services)


@router.post("/runners/{runner_id}/resume", response_model=RunnerView)
def resume_runner(
    runner_id: str, request: RunnerPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerView:
    """Clear a runner's pause brake — it resumes claiming on its next pull."""
    return _set_paused(runner_id, paused=False, by=request.by, services=services)


def _set_paused(runner_id: str, *, paused: bool, by: str, services: HubServices) -> RunnerView:
    if not services.fleet.set_paused(runner_id, paused=paused, by=by):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    services.events.publish_runner_changed(runner_id)
    liveness = services.fleet.get_liveness(runner_id)
    assert liveness is not None  # just set_paused succeeded, so the runner exists
    return runner_view(liveness)
