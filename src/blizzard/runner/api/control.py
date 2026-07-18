"""The runner-local control + summary endpoints on ``/api/runner`` (issue #43; ``GET`` issue #51).

The runner's own half of the pause brake, and the declarative pattern applied
locally: pause/start is *state on the runner singleton*, not a directive queue. Two
independent surfaces carry a pause and this is the machine-local one —

* **local** (this route) — "this runner says it won't try". Set here; since issue #45 it
  blocks every spawn site (FILL's claim, restart-resume, an answer-resume, ADVANCE's
  next-node, a requeue or claim-adopt respawn, and ADVANCE's judgement resume), defers
  REAP's kill of a stalled worker, and defers escalation at an exhausted retry budget —
  and is reachable with the hub down: the operator contract's standing requirement.
  Pause/start facts append and the flag derives from the newest.
* **hub** (``PATCH /runners/{id}`` at the hub, mirrored here by PULL) — the fleet-level
  brake. Untouched by this route; clear it where it was set (``blizzard hub resume``).

Effective paused is the OR of the two — FILL stops on either — so this route reports all
three values back rather than a single ambiguous ``paused``.

The edge is read-only over its wiring (``bzh:controller-read-only``): it writes through
the store the ``host`` composition root wired on ``app.state``. Without a store (the
store-free app used for OpenAPI export and unit tests) it answers 503 rather than
pretending. The CLI is a pure client of this route — it never opens the store itself.

``GET /api/runner`` (issue #51) is this same singleton's read side — ``blizzard runner
status``'s summary section: identity, both pause states, capacities, hub connectivity,
and the last tick. It holds the composition-root-wired :class:`~blizzard.runner.domain.status.RunnerStatusService`
rather than the store directly, since deriving hub reachability and capacity counts is
more than the store-facing ``_view`` below does for the pause fields alone.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.config import RunnerConfig
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

    Local to this machine and independent of the hub's brake: it works with the hub
    unreachable, and it neither reads nor writes the hub's flag. Every spawn site honors
    it — FILL, restart-resume, an answer-resume, ADVANCE's next-node, a requeue or
    claim-adopt respawn, and the judgement resume that elicits a verdict from an exited
    worker's session. REAP still reaps an orphan lease (nothing to kill, and its respawn
    is itself suppressed), but does not kill a worker that is merely stalled — pause is
    not a drain. Escalating a chunk to a human at an exhausted retry budget is deferred
    too, wherever it would happen (REAP, a rejected flush at PULL): a paused runner does
    not hand work off as unrecoverable while it waits. No retry is consumed at all: the
    budget counts lease mints, and the one mint site sits below the gate.

    A worker that *exits* while paused is **not** judged until the brake clears — judging
    it resumes its session headlessly, which is itself a spawn the brake forbids. It waits
    exactly like a suppressed respawn: the lease stays active, and ADVANCE retries the
    judgement every tick until the brake clears. A live worker already running is left
    alone throughout — this is not a drain, and it does not kill. Leases, routes, epochs,
    environments, and retry budgets are otherwise unchanged.
    """
    store: IWriteRunnerStore | None = getattr(request.app.state, "runner_store", None)
    config: RunnerConfig | None = getattr(request.app.state, "config", None)
    clock = getattr(request.app.state, "clock", None)
    if store is None or config is None or clock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner store not wired — start via `blizzard runner host`",
        )
    now = clock.now()
    # The brake and the report the board reads it from are one write: the hub can
    # only render what it holds, so a brake it is never told about would leave a runner
    # rendered as claiming after it has stopped — and PULL only mirrors hub→runner, so
    # nothing would ever repair it. The buffer delivers whenever the hub is next reachable,
    # which is what lets this route answer with the hub down.
    store.record_local_pause(
        config.runner_id,
        paused=request_body.paused,
        at=now,
        by=request_body.by,
        report_kind=RUNNER_LOCALLY_PAUSED if request_body.paused else RUNNER_LOCALLY_RESUMED,
        report_payload=json.dumps({"runner_id": config.runner_id, "by": request_body.by, "at": iso_utc(now)}),
    )
    return _view(store, config.runner_id)


@router.get("/runner", response_model=RunnerStatusView)
def get_runner(request: Request) -> RunnerStatusView:
    """The runner's machine-local summary: identity, pause states, capacities, hub
    connectivity, last tick (issue #51).

    Read-only over its wiring (``bzh:controller-read-only``): the edge holds only the
    composition-root-wired :class:`RunnerStatusService`, derived entirely from local
    store facts plus the injected clock — no hub call, so it is truthful with the hub
    unreachable. On the store-free app (OpenAPI export / unit tests) the service is
    unwired and the probe answers 503 rather than pretending.
    """
    service: RunnerStatusService | None = getattr(request.app.state, "runner_status", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner status service not wired — start via `blizzard runner host`",
        )
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
