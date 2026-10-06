"""Usage-limit exit classification and pause engagement.

Shared by a worker generation's own exit (:mod:`blizzard.runner.loop.steps`'s ``Advance``)
and a judge elicitation's own exit (:mod:`blizzard.runner.lifecycle.judgement.judgement`'s ``Judgement``):
both react to the same harness fact the same way — engage the local pause brake with a
reason naming the harness and its reset time, park the lease in place, consume no retry,
bump no epoch. The classifier is the adapter's own translated fact
(:meth:`~blizzard.runner.harness.adapter.IHarnessUsageLimits.classify_usage_limit`); this
module is where the loop decides what to do with it (``bzh:deterministic-shell``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import IHarnessUsageLimits
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.usage import UsageLimit
from blizzard.runner.leases import Lease
from blizzard.runner.lifecycle.attempt import Attempt, AttemptContext
from blizzard.runner.lifecycle.spawn import SpawnStores
from blizzard.runner.throttle.pause import PauseService, usage_limit_reason
from blizzard.runner.usage.repository import IReadUsageRepository, soonest_exhausted_reset

_log = get_logger("blizzard.runner.loop")

# The brake is written before the park — a crash after the brake but before the park
# leaves an exited, unparked lease that the next pass classifies again and completes.
_CP_WORKER_AFTER_BRAKE = crashpoint(
    "usagelimit.worker-after-brake.before-park",
    "usage-limit brake engaged for an exited worker generation; its park is not yet durable",
)
# Same shape for a judge elicitation, whose own record/output files must also clear before
# the park — a crash mid-way leaves a standing elicitation record the next pass re-reads.
_CP_JUDGE_AFTER_BRAKE = crashpoint(
    "usagelimit.judge-after-brake.before-park",
    "usage-limit brake engaged for an exited judge elicitation; its park is not yet durable",
)


class DeclaredSubscription(Protocol):
    @property
    def slug(self) -> str: ...


class UsageLimitStores(SpawnStores, Protocol):
    @property
    def usage(self) -> IReadUsageRepository: ...


class UsageLimitContext(AttemptContext, Protocol):
    @property
    def stores(self) -> UsageLimitStores: ...
    @property
    def subscriptions(self) -> Sequence[DeclaredSubscription]: ...


def classify_worker_usage_limit(
    ctx: UsageLimitContext, lease: Lease, output: str, lines: Sequence[str]
) -> UsageLimit | None:
    """This generation's own spawn/resume/nudge invocation, classified over ``output`` and
    ``lines`` — the caller's own single read of this generation's stdout and transcript
    range, shared with its provider-overload classification so neither pays for the other's
    read. ``None`` when not usage-limited, or the owner is unresolvable."""
    session = lease.session
    if session is None:
        return None
    harness = _usage_limit_adapter(ctx, session)
    if harness is None:
        return None
    return harness.classify_usage_limit(output, lines, ctx.clock.now())


def engage_and_park_worker(ctx: UsageLimitContext, lease: Lease, limit: UsageLimit) -> None:
    """Engage the brake for a limited worker generation, then park the lease in place —
    the worker has already exited, so there is nothing to kill."""
    session = lease.session
    assert session is not None  # only reached from a limit `classify_worker_usage_limit` returned
    _engage(ctx, session.harness_id, limit)
    _CP_WORKER_AFTER_BRAKE.reached()
    Attempt(ctx, lease).park_usage_limited()
    _log.warning(
        "usage-limit pause — worker generation parked, no retry consumed",
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        harness_id=session.harness_id,
        detail=limit.detail,
    )


def classify_judge_usage_limit(
    ctx: UsageLimitContext, lease: Lease, output: str, lines: Sequence[str]
) -> UsageLimit | None:
    """This generation's own judge elicitation, classified over its already-read output and
    transcript range (judge boundary to tail, shared with provider-overload classification)
    — ``None`` when not usage-limited."""
    session = lease.session
    if session is None:
        return None
    harness = _usage_limit_adapter(ctx, session)
    if harness is None:
        return None
    return harness.classify_usage_limit(output, lines, ctx.clock.now())


def engage_and_park_judge(ctx: UsageLimitContext, lease: Lease, limit: UsageLimit) -> None:
    """Engage the brake for a limited judge elicitation, then park the lease in place (the
    process has already exited — nothing to kill). The elicitation record is left standing:
    it marks the park as judge-side. A usage-limit pause never fails the attempt."""
    session = lease.session
    assert session is not None  # only reached from a limit `classify_judge_usage_limit` returned
    _engage(ctx, session.harness_id, limit)
    _CP_JUDGE_AFTER_BRAKE.reached()
    Attempt(ctx, lease).park_usage_limited()
    _log.warning(
        "usage-limit pause — judge elicitation parked, no retry consumed",
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        harness_id=session.harness_id,
        detail=limit.detail,
    )


def _engage(ctx: UsageLimitContext, harness_id: str, limit: UsageLimit) -> None:
    reason = _reason(ctx, harness_id, limit)
    PauseService(ctx.stores.pause, ctx.clock, events=ctx.events).engage(by="usage-limit", reason=reason)


def _reason(ctx: UsageLimitContext, harness_id: str, limit: UsageLimit) -> str:
    return usage_limit_reason(harness_id, limit.resets_at or _fallback_reset(ctx))


def _fallback_reset(ctx: UsageLimitContext) -> datetime | None:
    """The soonest pending reset every declared subscription itself last reported
    (:func:`~blizzard.runner.usage.repository.soonest_exhausted_reset`) — no
    harness-to-subscription mapping."""
    windows_by_slug = ctx.stores.usage.latest_external_usage_windows_by_slug([r.slug for r in ctx.subscriptions])
    return soonest_exhausted_reset(windows_by_slug, ctx.clock.now())


def _usage_limit_adapter(ctx: UsageLimitContext, session: SessionReference) -> IHarnessUsageLimits | None:
    """This session's usage-limit classifier, resolved through the registry's own
    ``usage_limits`` accessor (``bzh:seam-size-ceiling``).
    ``None`` on an unresolvable owner, never a raise: a lease already reaching this point has
    exited, and an owner this runner cannot dispatch to is `Judgement`/`Attempt`'s own
    escalation to make, not this classifier's."""
    try:
        return ctx.harnesses.usage_limits(session.harness_id)
    except (UnknownHarnessError, UnavailableHarnessError):
        return None
