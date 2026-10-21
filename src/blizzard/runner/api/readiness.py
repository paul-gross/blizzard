"""The host readiness probe — ``GET /api/ready``.

"Is this daemon fit to serve?" — its store reachable and at the expected schema
revision — beside each runner the host holds, serving or halted. Read-only over its
wiring (``bzh:controller-read-only``): it never opens the store itself. With no
readiness service wired, the probe reports ``ready=false`` with a detail rather than pretending."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.base import WireModel
from blizzard.wire.runner_host import HostRunnerStateView

router = APIRouter(prefix="/api", tags=["meta"])


class ReadinessResponse(WireModel):
    """The wire shape of a readiness reading."""

    ready: bool
    store_reachable: bool
    store_revision: str | None
    expected_revision: str | None
    detail: str
    runners: list[HostRunnerStateView] = []


@router.get("/ready")
def ready(request: Request) -> ReadinessResponse:
    wiring = RunnerWiring.of(request)
    registry = wiring.runners()
    runners = [
        *(HostRunnerStateView(runner_id=r.runner_id, name=r.name, state="serving") for r in registry.served),
        *(HostRunnerStateView(runner_id=r.id, name=r.name, state="halted") for r in registry.halted),
    ]
    service = wiring.maybe_readiness()
    if service is None:
        return ReadinessResponse(
            ready=False,
            store_reachable=False,
            store_revision=None,
            expected_revision=None,
            detail="readiness service not wired (store-free app)",
            runners=runners,
        )
    r = service.evaluate()
    return ReadinessResponse(
        ready=r.ready,
        store_reachable=r.store_reachable,
        store_revision=r.store_revision,
        expected_revision=r.expected_revision,
        detail=r.detail,
        runners=runners,
    )
