"""The runner-local external-subscription-usage diagnostics — ``GET /api/subscriptions``:
every declared subscription's newest sampling attempt, and on a miss the closed-set reason
telling a lapsed credential ("log in again") from an unreachable endpoint or an unparseable
response; beside it, the newest credential renewal's typed outcome. Reads the store directly
(``bzh:controller-read-only``), triggering no fresh sample or renewal of its own."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.config import RunnerConfig
from blizzard.runner.usage.repository import IReadUsageRepository
from blizzard.wire.runner_status import SubscriptionListResponse
from blizzard.wire.runner_status import SubscriptionView as SubscriptionViewWire

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/subscriptions", response_model=SubscriptionListResponse)
def list_subscriptions(request: Request) -> SubscriptionListResponse:
    """Every declared subscription's own newest sampling attempt and credential renewal."""
    wiring = RunnerWiring.of(request)
    return _subscription_list(wiring.config(), wiring.read_stores().usage)


def _subscription_list(config: RunnerConfig, usage: IReadUsageRepository) -> SubscriptionListResponse:
    declarations = config.resolved_subscriptions()
    slugs = [d.slug for d in declarations]
    attempts = usage.latest_external_usage_attempts_by_slug(slugs)
    renewals = usage.latest_credential_renewals_by_slug(slugs)
    items: list[SubscriptionViewWire] = []
    for declaration in declarations:
        attempt = attempts.get(declaration.slug)
        renewal = renewals.get(declaration.slug)
        items.append(
            SubscriptionViewWire(
                slug=declaration.slug,
                name=declaration.name,
                provider=declaration.provider,
                sampled_at=iso_utc(attempt.sampled_at) if attempt is not None else None,
                ok=attempt.ok if attempt is not None else None,
                miss_reason=SampleMissReason.recognized(attempt.miss_reason) if attempt is not None else None,
                renewal_attempted_at=iso_utc(renewal.attempted_at) if renewal is not None else None,
                renewal_result=renewal.result if renewal is not None else None,
                renewal_failure_reason=renewal.failure_reason if renewal is not None else None,
            )
        )
    return SubscriptionListResponse(items=items)
