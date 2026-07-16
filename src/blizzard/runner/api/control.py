"""The runner-local declarative control endpoint — ``PATCH /api/runner`` (issue #43).

The runner's own half of the pause brake, and the D-043 declarative pattern applied
locally: pause/start is *state on the runner singleton*, not a directive queue. Two
independent surfaces carry a pause and this is the machine-local one —

* **local** (this route) — "this runner says it won't try". Set here, adhered to by FILL,
  and reachable with the hub down: the operator contract's standing requirement
  ([api.md]). Pause/start facts append and the flag derives from the newest (D-004/D-039).
* **hub** (``PATCH /runners/{id}`` at the hub, mirrored here by PULL) — the fleet-level
  brake. Untouched by this route; clear it where it was set (``blizzard hub resume``).

Effective paused is the OR of the two — FILL stops on either — so this route reports all
three values back rather than a single ambiguous ``paused``.

The edge is read-only over its wiring (``bzh:controller-read-only``): it writes through
the store the ``host`` composition root wired on ``app.state``. Without a store (the
store-free app used for OpenAPI export and unit tests) it answers 503 rather than
pretending. The CLI is a pure client of this route — it never opens the store itself.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.repository import IWriteRunnerStore
from blizzard.wire.facts import RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED

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
    """Set this runner's own pause brake — it stops claiming; in-flight chunks run on (issue #43).

    Local to this machine and independent of the hub's brake: it works with the hub
    unreachable, and it neither reads nor writes the hub's flag.
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
    # The brake and the report the board reads it from are one write (D-069): the hub can
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
        report_payload=json.dumps({"runner_id": config.runner_id, "by": request_body.by, "at": now.isoformat()}),
    )
    return _view(store, config.runner_id)


def _view(store: IWriteRunnerStore, runner_id: str) -> RunnerControlView:
    local_paused = store.local_paused(runner_id)
    hub_paused = store.hub_paused(runner_id)
    return RunnerControlView(
        runner_id=runner_id,
        local_paused=local_paused,
        hub_paused=hub_paused,
        paused=local_paused or hub_paused,
    )
