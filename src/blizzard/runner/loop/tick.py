"""The tick driver — one pass of REAP → RESUME → PULL → FILL → ADVANCE (design/runner/loop.md).

``tick`` composes the step functions in order; it is the single synchronous pass the
``blizzard runner tick`` CLI verb and the periodic daemon driver both call. Because
startup recovery *is* REAP running first, a fresh daemon simply runs a tick — no special
recovery path. RESUME sits second, before ADVANCE could mistake a killed-mid-work worker
for a done declaration: on the first tick after a *graceful* restart it re-attaches each
in-flight session a shutdown marked (D-082); on every other tick it is a no-op. The tick
holds no state: every step reads and writes the runner store through the context's seams.
"""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.steps import advance, fill, pull, reap, resume

_log = get_logger("blizzard.runner.loop")


def tick(ctx: LoopContext) -> None:
    """Run one reconciliation pass. Idempotent; safe to call on startup and per-timer."""
    _log.debug("tick start", runner_id=ctx.config.runner_id)
    reap(ctx)
    resume(ctx)
    pull(ctx)
    fill(ctx)
    advance(ctx)
    _log.debug("tick end", runner_id=ctx.config.runner_id)
