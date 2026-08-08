"""Runner routes — the **operator** half of the fleet registry: list, read, pause,
resume, and enroll (issue #87, #104). The runner-authenticated half is
:mod:`blizzard.hub.api.fleet`, which reuses this module's :func:`runner_view`.

Controllers stay read-only over the store (``bzh:controller-read-only``);
``reject_runner_principal`` confines a runner's bearer token to the fleet router."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, ClassVar

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import FLEET_VIEW, RUNNER_PAUSE
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.registry import ExternalSubscriptionUsageView as UsageSample
from blizzard.hub.domain.registry import RunnerLiveness
from blizzard.wire.runner import (
    ExternalSubscriptionUsageView,
    ExternalSubscriptionUsageWindowView,
    RunnerEnrollmentResponse,
    RunnerListResponse,
    RunnerPauseRequest,
    RunnerView,
)
from blizzard.wire.sse import RunnerChangeKind

router = APIRouter(prefix="/api", tags=["runners"], dependencies=[Depends(reject_runner_principal)])


@dataclass(frozen=True)
class RunnerBrake:
    """One operator write of a runner's fleet pause brake. Which way the brake moves is
    the subclass's, and is the only thing that differs between the two verbs."""

    services: HubServices
    runner_id: str
    by: str

    paused: ClassVar[bool]
    kind: ClassVar[RunnerChangeKind]

    def set(self) -> RunnerView:
        """Write the fact, publish the frame, and read the runner back; 404 on an unknown one."""
        fact_id = self.services.fleet.set_paused(self.runner_id, paused=self.paused, by=self.by)
        if fact_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {self.runner_id}")
        self.services.events.publish_runner_changed(
            self.runner_id, kind=self.kind, by=self.by, key=f"runner_pause_facts:{fact_id}"
        )
        liveness = self.services.fleet.get_liveness(self.runner_id)
        assert liveness is not None  # just set_paused succeeded, so the runner exists
        return runner_view(liveness, now=self.services.clock.now())


class Paused(RunnerBrake):
    paused = True
    kind = "paused"


class Resumed(RunnerBrake):
    paused = False
    kind = "resumed"


def runner_view(liveness: RunnerLiveness, *, now: datetime) -> RunnerView:
    r = liveness.registration
    usage = UsageSample.of(r, now=now)
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
        env_capacity=r.env_capacity,
        external_subscription_usage=(
            ExternalSubscriptionUsageView(
                sampled_at=iso_utc(usage.sampled_at),
                windows=[
                    ExternalSubscriptionUsageWindowView(
                        window=w.window,
                        utilization_pct=w.utilization_pct,
                        resets_at=iso_utc(w.resets_at),
                        window_seconds=w.window_seconds,
                    )
                    for w in usage.windows
                ],
            )
            if usage is not None
            else None
        ),
    )


@router.post(
    "/runners/{runner_id}/enrollments",
    response_model=RunnerEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(RUNNER_PAUSE))],
)
def enroll_runner(runner_id: str, services: Annotated[HubServices, Depends(get_services)]) -> RunnerEnrollmentResponse:
    """Mint (or rotate) ``runner_id``'s bearer token — the plaintext is returned once;
    the store keeps only its sha256 hash from here on (issue #86a).

    Requires an existing registration (404 otherwise): enrollment is a deliberate act on
    a known runner, never a trust-on-first-use grant to an unregistered name."""
    liveness = services.fleet.get_liveness(runner_id)
    if liveness is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    token = services.enrollment.enroll(liveness.registration)
    return RunnerEnrollmentResponse(runner_id=runner_id, token=token)


@router.get("/runners", response_model=RunnerListResponse, dependencies=[Depends(require(FLEET_VIEW))])
def list_runners(services: Annotated[HubServices, Depends(get_services)]) -> RunnerListResponse:
    """The fleet registry — every runner with derived liveness + paused state."""
    now = services.clock.now()
    return RunnerListResponse(runners=[runner_view(item, now=now) for item in services.fleet.list_with_liveness()])


@router.get("/runners/{runner_id}", response_model=RunnerView, dependencies=[Depends(require(FLEET_VIEW))])
def get_runner(runner_id: str, services: Annotated[HubServices, Depends(get_services)]) -> RunnerView:
    """One runner's derived liveness + paused state — the operator's detail read,
    symmetric with the list. 404 on unknown."""
    liveness = services.fleet.get_liveness(runner_id)
    if liveness is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    return runner_view(liveness, now=services.clock.now())


@router.post("/runners/{runner_id}/pause", response_model=RunnerView, dependencies=[Depends(require(RUNNER_PAUSE))])
def pause_runner(
    runner_id: str, request: RunnerPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerView:
    """Set a runner's pause brake — no new claims; in-flight chunks run on."""
    return Paused(services, runner_id, request.by).set()


@router.post("/runners/{runner_id}/resume", response_model=RunnerView, dependencies=[Depends(require(RUNNER_PAUSE))])
def resume_runner(
    runner_id: str, request: RunnerPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerView:
    """Clear a runner's pause brake — it resumes claiming on its next pull."""
    return Resumed(services, runner_id, request.by).set()
