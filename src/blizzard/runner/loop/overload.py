"""Provider-overload classification and backoff (blizzard#595).

Shared by a worker generation's own exit and a judge elicitation's own exit: both react to
an overload fact the same way — record it and, short of the streak limit, resume in place
after a bounded, growing wait, spending no retry and bumping no epoch. Imports neither
``judgement`` nor ``dormant`` (D5's own composition boundary)."""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.domain.overload import (
    BACKOFF_LIMIT,
    InvocationKind,
    backing_off_facts,
    backoff_delay,
)
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.overload import ProviderOverload
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.loop.context import LoopContext

__all__ = [
    "backing_off_facts",
    "classify_judge_overload",
    "classify_worker_overload",
    "record_judge_overload",
    "record_worker_overload",
    "reset_if_streak_open",
]

_log = get_logger("blizzard.runner.loop")


def classify_worker_overload(ctx: LoopContext, lease: LeaseRecord, *, generation: int) -> ProviderOverload | None:
    """This generation's own spawn/resume/nudge invocation, classified — ``None`` when it
    did not exit on a provider overload, an unresolvable owner, or a session-less lease."""
    session = lease.session
    if session is None:
        return None
    harness = _full_adapter(ctx, session)
    if harness is None:
        return None
    output = ctx.worker_files.read_stdout(lease.lease_id, generation)
    bindings = ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
    lines = ctx.usage.worker_transcript_lines(lease, bindings, generation=generation)
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
    ctx: LoopContext, lease: LeaseRecord, output: str, *, generation: int
) -> ProviderOverload | None:
    """This generation's own judge elicitation, classified over its output file plus its
    own transcript range (judge boundary to tail) — ``None`` when not overloaded."""
    session = lease.session
    if session is None:
        return None
    harness = _full_adapter(ctx, session)
    if harness is None:
        return None
    bindings = ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
    lines = ctx.usage.judge_transcript_lines(lease, bindings, generation=generation)
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
            streak=f"{streak_ordinal}/{BACKOFF_LIMIT}",
            resume_after=iso_utc(resume_after) if resume_after is not None else None,
            detail=overload.detail,
        )
    else:
        _log.warning(
            "provider overload — streak limit reached, falling through",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            invocation_kind=invocation_kind,
            streak=f"{streak_ordinal}/{BACKOFF_LIMIT}",
            detail=overload.detail,
        )
    return backing_off


def _full_adapter(ctx: LoopContext, session: SessionReference) -> IHarnessAdapter | None:
    """The full adapter, unlike ``ctx.adapter_for``'s own ``IHarnessLifecycleAndVerdict``
    narrowing — classification needs ``IHarnessProviderOverload`` too. ``None`` on an
    unresolvable owner, never a raise: a lease already reaching this point has exited, and
    an owner this runner cannot dispatch to is `Judgement`/`Attempt`'s own escalation to
    make, not this classifier's."""
    try:
        return ctx.harnesses.adapter(session.harness_id)
    except (UnknownHarnessError, UnavailableHarnessError):
        return None
