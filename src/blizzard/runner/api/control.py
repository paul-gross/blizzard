"""The runner-local control + summary endpoints on ``/api/runner``.

Pause is *state on the runner singleton*, not a directive queue: pause/start facts append and
the flag derives from the newest. This route owns only the **local** brake, reachable with the
hub down; the fleet-level brake is the hub's own. Effective paused is the OR of the two, so all
three values are reported back rather than one ambiguous ``paused``."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.status.view import RunnerStatusService
from blizzard.wire.runner_status import CapacitiesView, HubConnectivityView, PauseStateView, RunnerStatusView

router = APIRouter(prefix="/api", tags=["runner"])


class RunnerControlView(BaseModel):
    """The runner singleton's derived pause state."""

    runner_id: str | None  # the hub-minted id of the latest registration; None before the first
    runner_name: str  # the name the hub recorded then, else the configured one
    local_paused: bool  # this runner's own brake
    hub_paused: bool  # the hub's brake, as last mirrored
    paused: bool  # effective: the OR of the two


class RunnerControlPatch(BaseModel):
    """Declarative controls on the runner singleton — ``paused`` now, routing knobs post-MVP."""

    paused: bool
    by: str = "operator"  # who flipped it — recorded on the fact


@router.patch("/runner", response_model=RunnerControlView)
def patch_runner(request_body: RunnerControlPatch, request: Request) -> RunnerControlView:
    """Set this runner's own pause brake — it starts no new workers.

    Independent of the hub's brake: it works with the hub unreachable, and neither reads nor
    writes the hub's flag. Not a drain: a live worker is left running."""
    wiring = RunnerWiring.of(request)
    status = wiring.status()
    wiring.pause().set_local_pause(paused=request_body.paused, by=request_body.by)
    summary = status.summary()
    return RunnerControlView(
        runner_id=summary.runner_id,
        runner_name=summary.runner_name,
        local_paused=summary.pause.local,
        hub_paused=summary.pause.hub,
        paused=summary.pause.effective,
    )


@router.get("/runner", response_model=RunnerStatusView)
def get_runner(request: Request) -> RunnerStatusView:
    """The runner's machine-local summary: identity, pause states, capacities, hub
    connectivity, last tick.

    Derived entirely from local store facts plus the injected clock — no hub call, so it
    is truthful with the hub unreachable. An unwired service answers 503."""
    return _runner_status_view(RunnerWiring.of(request).status())


def _runner_status_view(service: RunnerStatusService) -> RunnerStatusView:
    summary = service.summary()
    return RunnerStatusView(
        runner_id=summary.runner_id,
        runner_name=summary.runner_name,
        workspace_id=summary.workspace_id,
        pause=PauseStateView(
            local=summary.pause.local,
            hub=summary.pause.hub,
            effective=summary.pause.effective,
            local_reason=summary.pause.local_reason,
        ),
        capacities=CapacitiesView(
            max_agents=summary.capacities.max_agents, used=summary.capacities.used, free=summary.capacities.free
        ),
        hub=HubConnectivityView(
            endpoint=summary.hub.endpoint,
            reachable=summary.hub.reachable,
            last_contact_at=iso_utc(summary.hub.last_contact_at) if summary.hub.last_contact_at is not None else None,
            buffer_depth=summary.hub.buffer_depth,
        ),
        last_tick_at=iso_utc(summary.last_tick_at) if summary.last_tick_at is not None else None,
        gates=list(summary.gates),
    )
