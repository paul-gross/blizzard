"""Usage-limit exit classification and the mark.

Shared by a worker generation's own exit (:mod:`blizzard.runner.loop.steps`'s ``Advance``)
and a judge elicitation's own exit (:mod:`blizzard.runner.lifecycle.judgement.judgement`'s ``Judgement``):
both react to the same harness fact the same way — mark the limit on the subscription the runner spends, or on the
runner itself when it spends none, so the floor holds back every runner spending it; park the lease in place, consume
no retry, bump no epoch. No brake is engaged: a usage limit is a throttle, never a breaker. The classifier is the
adapter's own translated fact
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
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.overload import InvocationKind
from blizzard.runner.lifecycle.attempt import Attempt, AttemptContext
from blizzard.runner.lifecycle.spawn import SpawnConfig, SpawnHostStores, SpawnStores
from blizzard.runner.throttle.gate import GateSubscription
from blizzard.runner.throttle.usage_limits import (
    IWriteRunnerUsageLimitRepository,
    IWriteSubscriptionUsageLimitRepository,
    UsageLimitMark,
)
from blizzard.runner.usage.repository import ExternalUsageSampleSummary, IReadExternalUsageRepository

_log = get_logger("blizzard.runner.loop")

# The mark is written before the park — a crash after the mark but before the park leaves an exited, unparked lease
# that the next pass classifies again, replays the mark as a no-op under its invocation, and parks.
_CP_WORKER_AFTER_MARK = crashpoint(
    "usagelimit.worker-after-mark.before-park",
    "usage-limit mark written for an exited worker generation; its park is not yet durable",
)
# Same shape for a judge elicitation, whose own record/output files must also clear before
# the park — a crash mid-way leaves a standing elicitation record the next pass re-reads.
_CP_JUDGE_AFTER_MARK = crashpoint(
    "usagelimit.judge-after-mark.before-park",
    "usage-limit mark written for an exited judge elicitation; its park is not yet durable",
)


class DeclaredSubscription(GateSubscription, Protocol): ...


class UsageLimitStores(SpawnStores, Protocol):
    @property
    def usage_limits(self) -> IWriteRunnerUsageLimitRepository: ...


class UsageLimitHostStores(SpawnHostStores, Protocol):
    @property
    def external_usage(self) -> IReadExternalUsageRepository: ...
    @property
    def usage_limits(self) -> IWriteSubscriptionUsageLimitRepository: ...


class UsageLimitContext(AttemptContext, Protocol):
    @property
    def stores(self) -> UsageLimitStores: ...
    @property
    def host_stores(self) -> UsageLimitHostStores: ...
    @property
    def config(self) -> SpawnConfig: ...
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


def mark_and_park_worker(ctx: UsageLimitContext, lease: Lease, limit: UsageLimit, *, generation: int) -> None:
    """Mark the limit ``generation`` met, then park the lease in place — the worker has already exited, so there is
    nothing to kill."""
    session = lease.session
    assert session is not None  # only reached from a limit `classify_worker_usage_limit` returned
    _mark(ctx, lease, session.harness_id, limit, generation=generation, kind="worker")
    _CP_WORKER_AFTER_MARK.reached()
    Attempt(ctx, lease).park_usage_limited()
    _log.warning(
        "usage-limit mark — worker generation parked, no retry consumed",
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


def mark_and_park_judge(ctx: UsageLimitContext, lease: Lease, limit: UsageLimit, *, generation: int) -> None:
    """Mark the limit a judge elicitation of ``generation`` met, then park the lease in place (the process has already
    exited — nothing to kill). The elicitation record is left standing: it marks the park as judge-side. A usage-limit
    park never fails the attempt."""
    session = lease.session
    assert session is not None  # only reached from a limit `classify_judge_usage_limit` returned
    _mark(ctx, lease, session.harness_id, limit, generation=generation, kind="judge")
    _CP_JUDGE_AFTER_MARK.reached()
    Attempt(ctx, lease).park_usage_limited()
    _log.warning(
        "usage-limit mark — judge elicitation parked, no retry consumed",
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        harness_id=session.harness_id,
        detail=limit.detail,
    )


def _mark(
    ctx: UsageLimitContext, lease: Lease, harness_id: str, limit: UsageLimit, *, generation: int, kind: InvocationKind
) -> None:
    """Write one mark — to the subscription the runner spends, else to the runner itself. A replay of an invocation
    already marked writes nothing, so a crash between the mark and the park re-marks as a no-op."""
    marked_at = ctx.clock.now()
    slug = ctx.config.subscription
    sample = ctx.host_stores.external_usage.external_usage_sample_at(slug, marked_at) if slug is not None else None
    held = _held_window(sample, limit.resets_at)
    mark = UsageLimitMark(
        marked_at=marked_at,
        resets_at=limit.resets_at,
        window=held[0] if held is not None else None,
        window_resets_at=held[1] if held is not None else None,
        harness_id=harness_id,
        detail=limit.detail,
        lease_id=lease.lease_id,
        epoch=lease.epoch,
        generation=generation,
        invocation_kind=kind,
    )
    if slug is None:
        ctx.stores.usage_limits.record_usage_limit_mark(mark)
    else:
        ctx.host_stores.usage_limits.record_usage_limit_mark(slug, mark)


def _held_window(sample: ExternalUsageSampleSummary | None, resets_at: datetime | None) -> tuple[str, datetime] | None:
    """The window the limit fell in, and its period's reset: the one whose reset, in the newest sample at or before the
    mark, is the reset the harness named, to the minute — ``None`` when the harness named none, no sample stands, or no
    window carries it."""
    if sample is None or resets_at is None:
        return None
    named = _to_minute(resets_at)
    return next(((w.window, w.resets_at) for w in sample.windows if _to_minute(w.resets_at) == named), None)


def _to_minute(at: datetime) -> datetime:
    return at.replace(second=0, microsecond=0)


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
