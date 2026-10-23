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
from blizzard.runner.hub.identity import IdentityVerdict
from blizzard.runner.status.view import RunnerStatusService
from blizzard.runner.throttle.floor import FLOOR_UNCONFIRMED_GRACE, FloorAnswer
from blizzard.runner.throttle.levers import LeverAnswer
from blizzard.runner.throttle.report import FLOOR_LEVER
from blizzard.wire.runner import VersionWindowView
from blizzard.wire.runner_status import (
    BindingCapacityView,
    CapacitiesView,
    FloorClearStatusView,
    FloorHoldView,
    FloorStatusView,
    HubConnectivityView,
    IdentityVerdictView,
    PauseStateView,
    RoutingConflictView,
    RunnerStatusView,
    ServedProjectStatusView,
    ThrottleLeverView,
    ThrottleStatusView,
    UnmatchedBindingView,
)

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


@router.patch(
    "/runner",
    response_model=RunnerControlView,
    responses={503: {"description": "The runner's store is bound to no runner id."}},
)
def patch_runner(request_body: RunnerControlPatch, request: Request) -> RunnerControlView:
    """Set this runner's own pause brake — it starts no new workers.

    Independent of the hub's brake: it works with the hub unreachable, and neither reads nor
    writes the hub's flag. Not a drain: a live worker is left running. Refused with 503 while the
    store is bound to no runner id, since the brake is a fact of the runner the store is bound to."""
    wiring = RunnerWiring.of(request)
    wiring.require_bound()
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
        tenant_id=summary.tenant_id,
        tenant_name=summary.tenant_name,
        workspace_id=summary.workspace_id,
        pause=PauseStateView(
            local=summary.pause.local,
            hub=summary.pause.hub,
            effective=summary.pause.effective,
            local_reason=summary.pause.local_reason,
        ),
        throttle=_throttle_view(summary.throttle) if summary.throttle is not None else None,
        capacities=CapacitiesView(
            used=summary.capacities.used,
            agents=summary.capacities.agents,
            free=summary.capacities.free,
            bindings=[
                BindingCapacityView(
                    project=b.project,
                    project_id=b.project_id,
                    workspace_id=b.workspace_id,
                    agents=b.agents,
                    used=b.used,
                    free=b.free,
                )
                for b in summary.capacities.bindings
            ],
        ),
        hub=HubConnectivityView(
            endpoint=summary.hub.endpoint,
            reachable=summary.hub.reachable,
            last_contact_at=iso_utc(summary.hub.last_contact_at) if summary.hub.last_contact_at is not None else None,
            buffer_depth=summary.hub.buffer_depth,
            hub_version=summary.hub.version.hub_version,
            window=(
                VersionWindowView(floor=window.floor, ceiling=window.ceiling)
                if (window := summary.hub.version.window) is not None
                else None
            ),
        ),
        last_tick_at=iso_utc(summary.last_tick_at) if summary.last_tick_at is not None else None,
        gates=list(summary.gates),
        identity=_identity_view(summary.identity) if summary.identity is not None else None,
        served_projects=(
            [
                ServedProjectStatusView(
                    entry=p.entry,
                    project_id=p.project_id,
                    slug=p.slug,
                    name=p.name,
                    color=p.color,
                    resolved=p.resolved(),
                    answered=p.answered,
                )
                for p in summary.served_projects
            ]
            if summary.served_projects is not None
            else None
        ),
        unmatched_bindings=[
            UnmatchedBindingView(
                chunk_id=b.chunk_id,
                environment_id=b.environment_id,
                workdir=b.workdir,
                held_since=iso_utc(b.bound_at),
            )
            for b in summary.unmatched_bindings
        ],
        routing_conflicts=[
            RoutingConflictView(project_id=c.project_id, entries=list(c.entries)) for c in summary.routing_conflicts
        ],
    )


def _throttle_view(status) -> ThrottleStatusView:
    floor = status.verdict.floor
    hold = floor.hold
    clear = floor.clear
    return ThrottleStatusView(
        state=status.state,
        declining=[_declining_name(answer) for answer in status.verdict.declining],
        floor=FloorStatusView(
            subscription=floor.slug,
            admits=floor.admits,
            hold=FloorHoldView(
                kind=hold.kind,
                harness_id=hold.harness_id,
                window=hold.window,
                expected_reset=iso_utc(hold.expected_reset).replace("+00:00", "Z"),
                awaits_sample=hold.awaits_sample,
                grace_opens_at=iso_utc(hold.expected_reset + FLOOR_UNCONFIRMED_GRACE).replace("+00:00", "Z")
                if hold.awaits_sample
                else None,
            )
            if hold is not None
            else None,
            clear=FloorClearStatusView(cleared_at=iso_utc(clear.cleared_at).replace("+00:00", "Z"), by=clear.by)
            if clear is not None
            else None,
        ),
        levers=[_lever_view(lever) for lever in status.verdict.levers],
        on_decline=status.on_decline,
        on_usage_limit=status.on_usage_limit,
    )


def _declining_name(answer: FloorAnswer | LeverAnswer) -> str:
    return answer.lever.value if isinstance(answer, LeverAnswer) else FLOOR_LEVER


def _lever_view(lever: LeverAnswer) -> ThrottleLeverView:
    return ThrottleLeverView(
        name=lever.lever.value,
        setting=lever.setting,
        reading=f"{lever.reading}, {lever.in_force}" if lever.in_force else lever.reading,
        admits=lever.admits,
        until=iso_utc(lever.until).replace("+00:00", "Z") if lever.until is not None else None,
    )


def _identity_view(verdict: IdentityVerdict) -> IdentityVerdictView:
    return IdentityVerdictView(
        state=verdict.state,
        store_runner_id=verdict.store_runner_id,
        token_runner_id=verdict.token_runner_id,
        checked_at=iso_utc(verdict.checked_at) if verdict.checked_at is not None else None,
        reset_command=verdict.reset_command,
        reason=verdict.reason,
    )
