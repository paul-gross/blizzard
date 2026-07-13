"""The tick driver — one pass of REAP → PULL → FILL → ADVANCE (design/runner/loop.md).

``tick`` composes the four step functions in order; it is the single synchronous
pass the ``blizzard runner tick`` CLI verb and the periodic daemon driver both call.
Because startup recovery *is* REAP running first, a fresh daemon simply runs a tick
— no special recovery path. The tick holds no state: every step reads and writes the
runner store through the context's seams.
"""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.steps import advance, fill, pull, reap

_log = get_logger("blizzard.runner.loop")


def tick(ctx: LoopContext) -> None:
    """Run one reconciliation pass. Idempotent; safe to call on startup and per-timer."""
    _log.debug("tick start", runner_id=ctx.config.runner_id)
    reap(ctx)
    pull(ctx)
    fill(ctx)
    advance(ctx)
    _log.debug("tick end", runner_id=ctx.config.runner_id)
