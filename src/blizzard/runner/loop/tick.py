"""The tick driver — CEILING → REAP → RESUME → PULL → FILL → ADVANCE → TRANSCRIPT DRAIN → SAMPLE.

``tick`` composes the steps in order — the single synchronous pass both the CLI verb and
the periodic daemon driver call. Order is load-bearing throughout — each step's own inline
comment below states why its position matters."""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.steps import (
    Advance,
    ExternalUsageSample,
    Fill,
    Pull,
    Reap,
    Resume,
    SpendCeiling,
)
from blizzard.runner.loop.transcript_drain import TranscriptDrain

_log = get_logger("blizzard.runner.loop")


def tick(ctx: LoopContext) -> None:
    """Run one reconciliation pass. Idempotent; safe to call on startup and per-timer."""
    _log.debug("tick start", runner_id=ctx.config.runner_id)
    # Stamp liveness first (issue #13), so a pass that dies mid-step still leaves the beat
    # proving the daemon reached it — the reference the next startup's scan ages against.
    ctx.store.record_daemon_liveness(runner_id=ctx.config.runner_id, alive_at=ctx.clock.now())
    # The spend-ceiling kill-switch (issue #61b) — first, so it brakes the same tick it fires in.
    SpendCeiling(ctx).run()
    Reap(ctx).run()  # startup recovery IS reap running early
    Resume(ctx).run()  # before ADVANCE — else a killed-mid-work worker reads as done
    Pull(ctx).run()
    Fill(ctx).run()
    Advance(ctx).run()
    # After every fact-lane-draining step (D3, issue #246), so a wedged or slow, bounded
    # transcript flush delays nothing fleet-truth-bearing.
    TranscriptDrain(ctx).run()
    # Last (issue #218) — its own docstring reserves this position; still safe to run
    # before or after TranscriptDrain, since either's fact-lane enqueue waits for PULL anyway.
    ExternalUsageSample(ctx).run()
    _log.debug("tick end", runner_id=ctx.config.runner_id)
