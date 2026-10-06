"""The tick driver — CEILING → REAP → RESUME → PULL → FILL → ADVANCE → TRANSCRIPT DRAIN →
RETENTION → CONTEXT → SAMPLE.

``tick`` composes the steps in order — the single synchronous pass both the CLI verb and
the periodic daemon driver call. Order is load-bearing throughout — each step's own inline
comment below states why its position matters."""

from __future__ import annotations

import dataclasses
from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.capability_snapshot import TickCapabilities
from blizzard.runner.hub.chunk_status_cache import MemoizingChunkViewCache
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.steps import (
    Advance,
    ContextSample,
    ExternalUsageSample,
    Fill,
    Pull,
    Reap,
    Resume,
    Retention,
    SpendCeiling,
)
from blizzard.runner.tracing.platform import TICK_STEP
from blizzard.runner.transcripts.transcript_drain import TranscriptDrain

_log = get_logger("blizzard.runner.loop")


class _Step(Protocol):
    def __init__(self, ctx: LoopContext) -> None: ...

    def run(self) -> object: ...


def _traced(ctx: LoopContext, step: type[_Step]) -> None:
    """Run one step under its own span, named for the step class."""
    name = step.__name__
    with ctx.tracer.child(name, {TICK_STEP: name}):
        step(ctx).run()


def tick(ctx: LoopContext) -> None:
    """Run one reconciliation pass. Idempotent; safe to call on startup and per-timer."""
    with ctx.tracer.root("tick"):
        _tick(ctx)


def _tick(ctx: LoopContext) -> None:
    _log.debug("tick start", runner_name=ctx.config.runner_name)
    # Stamp liveness first, so a pass that dies mid-step still leaves the beat
    # proving the daemon reached it — the reference the next startup's scan ages against.
    ctx.stores.pause.record_daemon_liveness(alive_at=ctx.clock.now())
    # This tick's own memoized chunk-status cache — every step below shares
    # it via the rebound `ctx`, so a chunk read at more than one site this tick costs the
    # hub at most one round-trip. Primed with the ids every reconcile sweep below is about
    # to read anyway, so the common case pays for its reads once, up front.
    # This tick's own capability memo alongside it: PULL's registration push and every
    # FILL claim attempt's peek share one snapshot, so the per-binding binary version
    # probe building one runs once a tick rather than once per outbound call. `ctx.harness_versions`
    # (long-lived, not rebound here) is what keeps that one-per-tick probe from recurring
    # every tick in turn — this memo alone only bounds a single tick's own repeat calls.
    ctx = dataclasses.replace(
        ctx,
        chunk_views=MemoizingChunkViewCache(ctx.hub),
        capabilities=TickCapabilities(ctx.harnesses, ctx.harness_versions, ctx.harness_health),
    )
    ctx.chunk_views.prime(_primed_chunk_ids(ctx))
    # The spend-ceiling kill-switch — first, so it brakes the same tick it fires in.
    _traced(ctx, SpendCeiling)
    _traced(ctx, Reap)  # startup recovery IS reap running early
    _traced(ctx, Resume)  # before ADVANCE — else a killed-mid-work worker reads as done
    _traced(ctx, Pull)
    _traced(ctx, Fill)
    _traced(ctx, Advance)
    # After every fact-lane-draining step — bounded (the real bound
    # is `transcripts/transcript_drain.py`'s own, see there), so it delays nothing fleet-truth-bearing.
    _traced(ctx, TranscriptDrain)
    # Not load-bearing: each prune preserves what this tick's other readers see
    # — placed here only so a fact just enqueued isn't pruned the same tick it lands.
    _traced(ctx, Retention)
    # Observation only, so its position is not load-bearing: it gates nothing and nothing
    # reads its samples. Placed after ADVANCE so a lease that finished this tick is already
    # closed and not sampled one last time on its way out.
    _traced(ctx, ContextSample)
    # Last — its own docstring reserves this position; still safe to run
    # before or after TranscriptDrain, since either's fact-lane enqueue waits for PULL anyway.
    _traced(ctx, ExternalUsageSample)
    _log.debug("tick end", runner_name=ctx.config.runner_name)


def _primed_chunk_ids(ctx: LoopContext) -> set[str]:
    """The chunk ids this tick's reconcile sweeps and held-chunk poll are about to read
    anyway — primed in one batch call so their own first ``get()`` each is a cache hit."""
    return (
        {lease.chunk_id for lease in ctx.stores.lease_record.list_active_leases()}
        | {escalation.chunk_id for escalation in ctx.stores.escalations.open_escalations()}
        | ctx.stores.takeover.open_takeover_chunk_ids()
        | set(ctx.stores.environments.live_tenure_chunk_ids())
    )
