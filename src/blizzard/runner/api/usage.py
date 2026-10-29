"""The lease usage read — ``GET /api/leases/{lease_id}/usage``.

The usage facts one lease recorded, oldest first, each an invocation's own figures. **404**
means no lease with this id, ever; a closed lease stays readable. Read-only over its wiring
(``bzh:controller-read-only``)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.lease_scope import resolved_lease
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.runner_status import LeaseUsageResponse, UsageFactView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/usage", response_model=LeaseUsageResponse)
def get_lease_usage(lease_id: str, request: Request) -> LeaseUsageResponse:
    """The usage facts ``lease_id`` recorded — 404 iff no such lease."""
    lease = resolved_lease(lease_id, request)
    facts = RunnerWiring.of(request).read_stores().usage.lease_usage_facts(lease.lease_id)
    return LeaseUsageResponse(
        lease_id=lease.lease_id,
        items=[
            UsageFactView(
                kind=fact.kind,
                generation=fact.generation,
                node_id=fact.node_id,
                epoch=fact.epoch,
                model=fact.model,
                harness_id=fact.harness_id,
                input_tokens=fact.input_tokens,
                output_tokens=fact.output_tokens,
                cache_read_tokens=fact.cache_read_tokens,
                cache_create_tokens=fact.cache_create_tokens,
                cost_usd=fact.cost_usd,
                estimated_cost_usd=fact.estimated_cost_usd,
                recorded_at=iso_utc(fact.recorded_at),
            )
            for fact in facts
        ],
    )
