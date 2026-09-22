"""The runner-local external-subscription-usage diagnostics — ``GET /api/subscriptions``
(blizzard#504): every declared subscription's newest sampling attempt, and on a miss the
closed-set reason telling a lapsed credential ("log in again") from an unreachable endpoint
or an unparseable response. Reads the store directly (``bzh:controller-read-only``),
triggering no fresh sample of its own."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.usage import IReadUsageRepository
from blizzard.wire.runner_status import SubscriptionListResponse
from blizzard.wire.runner_status import SubscriptionView as SubscriptionViewWire

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/subscriptions", response_model=SubscriptionListResponse)
def list_subscriptions(request: Request) -> SubscriptionListResponse:
    """Every declared subscription's own newest sampling attempt."""
    wiring = RunnerWiring.of(request)
    return _subscription_list(wiring.config(), wiring.read_stores().usage)


def _subscription_list(config: RunnerConfig, usage: IReadUsageRepository) -> SubscriptionListResponse:
    items: list[SubscriptionViewWire] = []
    for declaration in config.resolved_subscriptions():
        attempt = usage.latest_external_usage_attempt(declaration.slug)
        items.append(
            SubscriptionViewWire(
                slug=declaration.slug,
                name=declaration.name,
                provider=declaration.provider,
                sampled_at=iso_utc(attempt.sampled_at) if attempt is not None else None,
                ok=attempt.ok if attempt is not None else None,
                miss_reason=attempt.miss_reason if attempt is not None else None,
                renewal=attempt.renewal if attempt is not None else None,
            )
        )
    return SubscriptionListResponse(items=items)
