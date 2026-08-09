"""The tick driver — one pass of CEILING → REAP → RESUME → PULL → FILL → ADVANCE →
TRANSCRIPT DRAIN → SAMPLE.

``tick`` composes the steps in order — the single synchronous pass both the CLI
verb and the periodic daemon driver call. The order is load-bearing: the spend ceiling
brakes the same tick it fires in; startup recovery *is* REAP running early; RESUME
precedes ADVANCE, which would otherwise read a killed-mid-work worker as done.
TranscriptDrain sits after every fact-lane step (D3, issue #246) — never chained to
PULL's own drain — so a wedged or slow transcript flush delays nothing fleet-truth-bearing;
it precedes SAMPLE only because that step's own docstring claims *last*."""

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
    # The spend-ceiling kill-switch (issue #61b) — first; see the module docstring.
    SpendCeiling(ctx).run()
    Reap(ctx).run()
    Resume(ctx).run()
    Pull(ctx).run()
    Fill(ctx).run()
    Advance(ctx).run()
    # After every fact-lane step (D3, issue #246) — see the module docstring.
    TranscriptDrain(ctx).run()
    # Last (issue #218) — see the module docstring.
    ExternalUsageSample(ctx).run()
    _log.debug("tick end", runner_id=ctx.config.runner_id)
