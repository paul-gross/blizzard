"""The runner-local harness-health diagnostics — ``GET /api/harness-health``.

Every configured harness binding's computed availability, its withholding cause when
unavailable, and every declared degradation regardless — the same evaluation its
registration push advertises to the hub as a single boolean. Reads the composition root's
own cache (``bzh:controller-read-only``), triggering no fresh probe of its own."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.harness.health import reported_health
from blizzard.runner.harness.health_cache import IReadHarnessHealth
from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.wire.runner_status import HarnessHealthListResponse
from blizzard.wire.runner_status import HarnessHealthView as HarnessHealthViewWire

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/harness-health", response_model=HarnessHealthListResponse)
def list_harness_health(request: Request) -> HarnessHealthListResponse:
    """Every configured harness binding's own computed health."""
    wiring = RunnerWiring.of(request)
    return _harness_health_list(wiring.harnesses(), wiring.harness_health())


def _harness_health_list(harnesses: IHarnessRegistry, health: IReadHarnessHealth) -> HarnessHealthListResponse:
    items: list[HarnessHealthViewWire] = []
    for harness_id in harnesses.known_harnesses:
        result = reported_health(harness_id, health.get(harness_id))
        items.append(
            HarnessHealthViewWire(
                harness_id=harness_id,
                version=health.displayed_version(harness_id),
                available=result.available,
                cause=result.cause.value if result.cause is not None else None,
                degradations=[d.summary for d in result.degradations],
                admitted_range=health.admitted_range(harness_id),
            )
        )
    return HarnessHealthListResponse(items=items)
