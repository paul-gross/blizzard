"""Provider-overload classification and backoff.

Shared by a worker generation's own exit and a judge elicitation's own exit: both react to
an overload fact the same way — record it and, short of the streak limit, resume in place
after a bounded, growing wait, spending no retry and bumping no epoch. Imports neither
``judgement`` nor ``dormant`` (a composition boundary)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.adapter import IHarnessProviderOverload
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.overload import ProviderOverload
from blizzard.runner.harness.registry import IHarnessRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.leases import Lease
from blizzard.runner.leases.overload import BACKOFF_LIMIT, InvocationKind, IWriteOverloadRepository, OverloadStreak

__all__ = [
    "OverloadContext",
    "OverloadStores",
    "classify_judge_overload",
    "classify_worker_overload",
    "record_judge_overload",
    "record_worker_overload",
    "reset_if_streak_open",
]

_log = get_logger("blizzard.runner.loop")


class OverloadStores(Protocol):
    @property
    def overload(self) -> IWriteOverloadRepository: ...


class OverloadContext(Protocol):
    @property
    def stores(self) -> OverloadStores: ...
    @property
    def clock(self) -> IClock: ...
    @property
    def events(self) -> IRunnerEventPublisher | None: ...
    @property
    def harnesses(self) -> IHarnessRegistry: ...


def classify_worker_overload(
    ctx: OverloadContext, lease: Lease, output: str, lines: Sequence[str]
) -> ProviderOverload | None:
    """This generation's own spawn/resume/nudge invocation, classified over ``output`` and
    ``lines`` — the caller's own single read of this generation's stdout and transcript
    range, shared with its usage-limit classification so neither pays for the other's read.
    ``None`` when not overloaded, or the owner is unresolvable."""
    session = lease.session
    if session is None:
        return None
    harness = _overload_adapter(ctx, session)
    if harness is None:
        return None
    return harness.classify_provider_overload(output, lines)


def record_worker_overload(ctx: OverloadContext, lease: Lease, overload: ProviderOverload, *, generation: int) -> bool:
    """Record this worker generation's overload fact. Returns ``True`` iff the lease should
    now back off in place — ``False`` on the streak's fall-through, where the caller
    proceeds on today's ordinary path (a worker judged as usual)."""
    return _record(
        ctx, lease, overload, generation=generation, invocation_kind="worker", invocation_identity=str(generation)
    )


def classify_judge_overload(
    ctx: OverloadContext, lease: Lease, output: str, lines: Sequence[str]
) -> ProviderOverload | None:
    """This generation's own judge elicitation, classified over its already-read output and
    transcript range (judge boundary to tail, shared with usage-limit classification)
    — ``None`` when not overloaded."""
    session = lease.session
    if session is None:
        return None
    harness = _overload_adapter(ctx, session)
    if harness is None:
        return None
    return harness.classify_provider_overload(output, lines)


def record_judge_overload(
    ctx: OverloadContext, lease: Lease, overload: ProviderOverload, *, generation: int, invocation_identity: str
) -> bool:
    """Record this judge elicitation's overload fact. Returns ``True`` iff the lease should
    now back off in place — ``False`` on the streak's fall-through, where the caller
    proceeds on today's ordinary path (a verdict-less judge fails as usual)."""
    return _record(
        ctx, lease, overload, generation=generation, invocation_kind="judge", invocation_identity=invocation_identity
    )


def reset_if_streak_open(ctx: OverloadContext, lease: Lease) -> None:
    """Close an open streak on a clean exit — written only when one is actually open,
    so a lease that has never overloaded never gains a reset row of its own."""
    if OverloadStreak(ctx.stores.overload.overload_streak(lease.lease_id, lease.epoch)).open:
        ctx.stores.overload.record_reset(lease_id=lease.lease_id, epoch=lease.epoch, at=ctx.clock.now())


def _record(
    ctx: OverloadContext,
    lease: Lease,
    overload: ProviderOverload,
    *,
    generation: int,
    invocation_kind: InvocationKind,
    invocation_identity: str,
) -> bool:
    streak = OverloadStreak(ctx.stores.overload.overload_streak(lease.lease_id, lease.epoch))
    candidate = streak.next_fact(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        epoch=lease.epoch,
        generation=generation,
        invocation_kind=invocation_kind,
        invocation_identity=invocation_identity,
        at=ctx.clock.now(),
    )
    standing = ctx.stores.overload.record_overload(
        lease_id=candidate.lease_id,
        chunk_id=candidate.chunk_id,
        epoch=candidate.epoch,
        generation=candidate.generation,
        invocation_kind=candidate.invocation_kind,
        invocation_identity=candidate.invocation_identity,
        streak_ordinal=candidate.streak_ordinal,
        observed_at=candidate.observed_at,
        resume_after=candidate.resume_after,
    )
    fact = candidate.settled(standing)
    resume_after = fact.resume_after
    if fact.backing_off:
        if ctx.events is not None:
            # The same `dormant` cause `park_on_ask`/`park_paused` already publish for
            # their own state flips — the SSE corpus gains no new kind.
            ctx.events.publish_lease_changed(lease.lease_id, lease.chunk_id, cause="dormant")
        _log.warning(
            "provider overload — backing off in place",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            invocation_kind=invocation_kind,
            streak=fact.streak_ordinal,
            backoff_limit=BACKOFF_LIMIT,
            resume_after=iso_utc(resume_after) if resume_after is not None else None,
            detail=overload.detail,
        )
    else:
        _log.warning(
            "provider overload — streak limit reached, falling through",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            invocation_kind=invocation_kind,
            streak=fact.streak_ordinal,
            backoff_limit=BACKOFF_LIMIT,
            detail=overload.detail,
        )
    return fact.backing_off


def _overload_adapter(ctx: OverloadContext, session: SessionReference) -> IHarnessProviderOverload | None:
    """This session's provider-overload classifier, resolved through the registry's own
    ``provider_overload`` accessor (``bzh:seam-size-ceiling``).
    ``None`` on an unresolvable owner, never a raise: a lease already reaching this point has
    exited, and an owner this runner cannot dispatch to is `Judgement`/`Attempt`'s own
    escalation to make, not this classifier's."""
    try:
        return ctx.harnesses.provider_overload(session.harness_id)
    except (UnknownHarnessError, UnavailableHarnessError):
        return None
