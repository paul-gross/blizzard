"""Provider-overload classification and backoff (blizzard#595).

Shared by a worker generation's own exit and a judge elicitation's own exit: both react to
an overload fact the same way — record it and, short of the streak limit, resume in place
after a bounded, growing wait, spending no retry and bumping no epoch. Imports neither
``judgement`` nor ``dormant`` (D5's own composition boundary)."""

from __future__ import annotations

from collections.abc import Sequence

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.domain.overload import BACKOFF_LIMIT, InvocationKind, backoff_delay
from blizzard.runner.harness.adapter import IHarnessProviderOverload
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.overload import ProviderOverload
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.loop.context import LoopContext

__all__ = [
    "classify_judge_overload",
    "classify_worker_overload",
    "record_judge_overload",
    "record_worker_overload",
    "reset_if_streak_open",
]

_log = get_logger("blizzard.runner.loop")


def classify_worker_overload(
    ctx: LoopContext, lease: LeaseRecord, output: str, lines: Sequence[str]
) -> ProviderOverload | None:
    """This generation's own spawn/resume/nudge invocation, classified over ``output`` and
    ``lines`` — the caller's own single read of this generation's stdout and transcript
    range, shared with its usage-limit classification so neither pays for the other's read
    (blizzard#595 F4). ``None`` when not overloaded, or the owner is unresolvable."""
    session = lease.session
    if session is None:
        return None
    harness = _overload_adapter(ctx, session)
    if harness is None:
        return None
    return harness.classify_provider_overload(output, lines)


def record_worker_overload(
    ctx: LoopContext, lease: LeaseRecord, overload: ProviderOverload, *, generation: int
) -> bool:
    """Record this worker generation's overload fact. Returns ``True`` iff the lease should
    now back off in place — ``False`` on the streak's fall-through, where the caller
    proceeds on today's ordinary path (a worker judged as usual)."""
    return _record(
        ctx, lease, overload, generation=generation, invocation_kind="worker", invocation_identity=str(generation)
    )


def classify_judge_overload(
    ctx: LoopContext, lease: LeaseRecord, output: str, lines: Sequence[str]
) -> ProviderOverload | None:
    """This generation's own judge elicitation, classified over its already-read output and
    transcript range (judge boundary to tail, shared with usage-limit classification —
    blizzard#595 F4) — ``None`` when not overloaded."""
    session = lease.session
    if session is None:
        return None
    harness = _overload_adapter(ctx, session)
    if harness is None:
        return None
    return harness.classify_provider_overload(output, lines)


def record_judge_overload(
    ctx: LoopContext, lease: LeaseRecord, overload: ProviderOverload, *, generation: int, invocation_identity: str
) -> bool:
    """Record this judge elicitation's overload fact. Returns ``True`` iff the lease should
    now back off in place — ``False`` on the streak's fall-through, where the caller
    proceeds on today's ordinary path (a verdict-less judge fails as usual)."""
    return _record(
        ctx, lease, overload, generation=generation, invocation_kind="judge", invocation_identity=invocation_identity
    )


def reset_if_streak_open(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Close an open streak on a clean exit (D5) — written only when one is actually open,
    so a lease that has never overloaded never gains a reset row of its own."""
    if ctx.stores.overload.overload_streak(lease.lease_id, lease.epoch) > 0:
        ctx.stores.overload.record_reset(lease_id=lease.lease_id, epoch=lease.epoch, at=ctx.clock.now())


def _record(
    ctx: LoopContext,
    lease: LeaseRecord,
    overload: ProviderOverload,
    *,
    generation: int,
    invocation_kind: InvocationKind,
    invocation_identity: str,
) -> bool:
    now = ctx.clock.now()
    streak_ordinal = ctx.stores.overload.overload_streak(lease.lease_id, lease.epoch) + 1
    backing_off = streak_ordinal < BACKOFF_LIMIT
    resume_after = now + backoff_delay(streak_ordinal) if backing_off else None
    ctx.stores.overload.record_overload(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        epoch=lease.epoch,
        generation=generation,
        invocation_kind=invocation_kind,
        invocation_identity=invocation_identity,
        streak_ordinal=streak_ordinal,
        observed_at=now,
        resume_after=resume_after,
    )
    if backing_off:
        if ctx.events is not None:
            # The same `dormant` cause `park_on_ask`/`park_paused` already publish for
            # their own state flips — the SSE corpus gains no new kind (D10).
            ctx.events.publish_lease_changed(lease.lease_id, lease.chunk_id, cause="dormant")
        _log.warning(
            "provider overload — backing off in place",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            invocation_kind=invocation_kind,
            streak=streak_ordinal,
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
            streak=streak_ordinal,
            backoff_limit=BACKOFF_LIMIT,
            detail=overload.detail,
        )
    return backing_off


def _overload_adapter(ctx: LoopContext, session: SessionReference) -> IHarnessProviderOverload | None:
    """This session's own adapter, narrowed to ``IHarnessProviderOverload`` — unlike
    ``ctx.adapter_for``'s own ``IHarnessLifecycleAndVerdict`` slice (``bzh:seam-size-ceiling``):
    a classifier depends on exactly the one method it calls rather than the full seam.
    ``None`` on an unresolvable owner, never a raise: a lease already reaching this point has
    exited, and an owner this runner cannot dispatch to is `Judgement`/`Attempt`'s own
    escalation to make, not this classifier's."""
    try:
        return ctx.harnesses.adapter(session.harness_id)
    except (UnknownHarnessError, UnavailableHarnessError):
        return None
