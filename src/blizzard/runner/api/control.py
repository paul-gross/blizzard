"""The runner-local control + summary endpoints on ``/api/runner`` (issue #43; ``GET`` issue #51).

Pause is *state on the runner singleton*, not a directive queue: pause/start facts append and
the flag derives from the newest. This route owns only the **local** brake, reachable with the
hub down; the fleet-level brake is the hub's own. Effective paused is the OR of the two, so all
three values are reported back rather than one ambiguous ``paused``."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from pydantic import BaseModel

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.runner.store.repository import IWriteRunnerStore
from blizzard.wire.facts import RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED
from blizzard.wire.runner_status import CapacitiesView, HubConnectivityView, PauseStateView, RunnerStatusView

router = APIRouter(prefix="/api", tags=["runner"])


class RunnerControlView(BaseModel):
    """The runner singleton's derived pause state (openapi-ts consumes this)."""

    runner_id: str
    local_paused: bool  # this runner's own brake — "I won't try"
    hub_paused: bool  # the hub's brake, as last mirrored by PULL
    paused: bool  # effective: the OR of the two — what FILL actually adheres to


class RunnerControlPatch(BaseModel):
    """Declarative controls on the runner singleton — ``paused`` now, routing knobs post-MVP."""

    paused: bool
    by: str = "operator"  # who flipped it — recorded on the fact


@router.patch("/runner", response_model=RunnerControlView)
def patch_runner(request_body: RunnerControlPatch, request: Request) -> RunnerControlView:
    """Set this runner's own pause brake — it starts no new workers (issue #45).

    Independent of the hub's brake: it works with the hub unreachable, and neither reads nor
    writes the hub's flag. Every spawn site honors it, and escalation at an exhausted budget is
    deferred. Not a drain: a live worker is left running, and no retry is consumed."""
    wiring = RunnerWiring.of(request)
    store, config = wiring.store(), wiring.config()
    now = wiring.clock().now()
    report_kind = RUNNER_LOCALLY_PAUSED if request_body.paused else RUNNER_LOCALLY_RESUMED
    # The brake and its upward report are one write: mirroring runs hub→runner only, so a
    # brake never reported up would never be repaired (tests/test_ingest_and_pause_verbs.py).
    seq = store.record_local_pause(
        config.runner_id,
        paused=request_body.paused,
        at=now,
        by=request_body.by,
        report_kind=report_kind,
        report_payload=json.dumps({"runner_id": config.runner_id, "by": request_body.by, "at": iso_utc(now)}),
    )
    events = wiring.events()
    if events is not None:
        events.publish_fact_changed(seq=seq, kind=report_kind, chunk_id=None, lease_id=None)
    return _view(store, config.runner_id)


@router.get("/runner", response_model=RunnerStatusView)
def get_runner(request: Request) -> RunnerStatusView:
    """The runner's machine-local summary: identity, pause states, capacities, hub
    connectivity, last tick (issue #51).

    Derived entirely from local store facts plus the injected clock — no hub call, so it
    is truthful with the hub unreachable. An unwired service answers 503."""
    return _runner_status_view(RunnerWiring.of(request).status())


def _runner_status_view(service: RunnerStatusService) -> RunnerStatusView:
    summary = service.summary()
    return RunnerStatusView(
        runner_id=summary.runner_id,
        workspace_id=summary.workspace_id,
        pause=PauseStateView(local=summary.pause.local, hub=summary.pause.hub, effective=summary.pause.effective),
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
    )


def _view(store: IWriteRunnerStore, runner_id: str) -> RunnerControlView:
    local_paused = store.local_paused(runner_id)
    hub_paused = store.hub_paused(runner_id)
    return RunnerControlView(
        runner_id=runner_id,
        local_paused=local_paused,
        hub_paused=hub_paused,
        paused=local_paused or hub_paused,
    )
