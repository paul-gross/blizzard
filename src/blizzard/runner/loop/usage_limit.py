"""Usage-limit exit classification and pause engagement (blizzard#594).

Shared by a worker generation's own exit (:mod:`blizzard.runner.loop.steps`'s ``Advance``)
and a judge elicitation's own exit (:mod:`blizzard.runner.loop.judgement`'s ``Judgement``):
both react to the same harness fact the same way — engage the local pause brake with a
reason naming the harness and its reset time, park the lease in place, consume no retry,
bump no epoch. The classifier is the adapter's own translated fact
(:meth:`~blizzard.runner.harness.adapter.IHarnessUsageLimits.classify_usage_limit`); this
module is where the loop decides what to do with it (``bzh:deterministic-shell``)."""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.domain.pause import PauseService
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.usage import UsageLimit
from blizzard.runner.loop.attempt import Attempt
from blizzard.runner.loop.context import LoopContext

_log = get_logger("blizzard.runner.loop")

# The brake is written before the park (D2) — a crash after the brake but before the park
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


def classify_worker_usage_limit(ctx: LoopContext, lease: LeaseRecord, *, generation: int) -> UsageLimit | None:
    """This generation's own spawn/resume/nudge invocation, classified — ``None`` when it
    was not usage-limited, an unresolvable owner, or a session-less lease."""
    session = lease.session
    if session is None:
        return None
    harness = _full_adapter(ctx, session)
    if harness is None:
        return None
    output = ctx.worker_files.read_stdout(lease.lease_id, generation)
    bindings = ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
    lines = ctx.usage.worker_transcript_lines(lease, bindings, generation=generation)
    return harness.classify_usage_limit(output, lines, ctx.clock.now())


def engage_and_park_worker(ctx: LoopContext, lease: LeaseRecord, limit: UsageLimit) -> None:
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
    ctx: LoopContext, lease: LeaseRecord, output: str, *, generation: int
) -> UsageLimit | None:
    """This generation's own judge elicitation, classified over its output file plus its
    own transcript range (judge boundary to tail) — ``None`` when not usage-limited."""
    session = lease.session
    if session is None:
        return None
    harness = _full_adapter(ctx, session)
    if harness is None:
        return None
    bindings = ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
    lines = ctx.usage.judge_transcript_lines(lease, bindings, generation=generation)
    return harness.classify_usage_limit(output, lines, ctx.clock.now())


def engage_and_park_judge(ctx: LoopContext, lease: LeaseRecord, limit: UsageLimit) -> None:
    """Engage the brake for a limited judge elicitation, then park the lease in place (the
    process has already exited — nothing to kill). The elicitation record is left standing,
    on purpose: :meth:`~blizzard.runner.loop.dormant.DormantSession.on_unpause` reads it back
    to tell a judge-side park from a worker-side one, and re-runs `Judgement` (a fresh
    elicitation) rather than waking a worker whose own turn already finished — clearing here
    would erase that signal. Never routed through ``Judgement._lost``: that path's staleness
    bound would eventually fail the attempt, exactly what a usage-limit pause must not do."""
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


def _engage(ctx: LoopContext, harness_id: str, limit: UsageLimit) -> None:
    reason = _reason(ctx, harness_id, limit)
    PauseService(ctx.stores.pause, ctx.clock, events=ctx.events).engage(
        ctx.config.runner_id, by="usage-limit", reason=reason
    )


def _reason(ctx: LoopContext, harness_id: str, limit: UsageLimit) -> str:
    resets_at = limit.resets_at or _fallback_reset(ctx)
    suffix = f" (resets {_minute_precision(resets_at)})" if resets_at is not None else ""
    return f"usage limit: {harness_id}{suffix}"


def _fallback_reset(ctx: LoopContext) -> datetime | None:
    """The soonest future reset among every declared subscription's own latest-sampled
    windows at or past 100% utilization (D4) — no harness-to-subscription mapping, just
    what every declared subscription itself last reported. ``None`` when nothing exhausted
    is on record, which the caller reads as "no reset time known", not "no limit"."""
    now = ctx.clock.now()
    soonest: datetime | None = None
    for resolved in ctx.subscriptions:
        for window in ctx.stores.usage.latest_external_usage_windows(resolved.slug):
            if window.utilization_pct < 100.0 or window.resets_at <= now:
                continue
            if soonest is None or window.resets_at < soonest:
                soonest = window.resets_at
    return soonest


def _minute_precision(value: datetime) -> str:
    return as_utc(value).strftime("%Y-%m-%dT%H:%MZ")


def _full_adapter(ctx: LoopContext, session: SessionReference) -> IHarnessAdapter | None:
    """The full adapter, unlike ``ctx.adapter_for``'s own ``IHarnessLifecycleAndVerdict``
    narrowing — classification needs ``IHarnessUsageLimits`` too. ``None`` on an
    unresolvable owner, never a raise: a lease already reaching this point has exited, and
    an owner this runner cannot dispatch to is `Judgement`/`Attempt`'s own escalation to make,
    not this classifier's."""
    try:
        return ctx.harnesses.adapter(session.harness_id)
    except (UnknownHarnessError, UnavailableHarnessError):
        return None
