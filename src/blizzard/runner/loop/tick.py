"""The tick driver — one pass of CEILING → REAP → RESUME → PULL → FILL → ADVANCE.

``tick`` composes the step functions in order; it is the single synchronous pass the
``blizzard runner tick`` CLI verb and the periodic daemon driver both call. The spend
ceiling check (issue #61b) runs first: a crossing it detects engages the local pause
brake before any later step in the *same* tick can spawn a worker or decide whether to
kill a stalled one. Because startup recovery *is* REAP running first (right after that
check), a fresh daemon simply runs a tick — no special recovery path. RESUME sits third,
before ADVANCE could mistake a killed-mid-work worker for a done declaration: on the
first tick after a restart it re-attaches each in-flight session marked for same-lease
resume — by the graceful shutdown hook (#12) or, when a ``kill -9`` / reboot skipped that
hook, by ``host``'s startup crash-recovery scan (#13); on every other tick it is a no-op.
The tick holds no state: every step reads and writes the runner store through the
context's seams.
"""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.steps import advance, check_spend_ceiling, fill, pull, reap, resume

_log = get_logger("blizzard.runner.loop")


def tick(ctx: LoopContext) -> None:
    """Run one reconciliation pass. Idempotent; safe to call on startup and per-timer."""
    _log.debug("tick start", runner_id=ctx.config.runner_id)
    # Stamp liveness first, so the newest stamp is when the daemon was last known alive —
    # the crash-time reference the next startup's recovery scan classifies staleness against
    # (issue #13). Recorded before the steps, not after, so a pass that dies mid-step still
    # leaves the beat that proves the daemon reached it.
    ctx.store.record_daemon_liveness(runner_id=ctx.config.runner_id, alive_at=ctx.clock.now())
    # The spend-ceiling kill-switch (issue #61b) runs before every other step so a crossing
    # detected this tick is already engaged — via the local pause brake — by the time REAP
    # decides whether to kill a stalled worker and FILL decides whether to spawn one.
    check_spend_ceiling(ctx)
    reap(ctx)
    resume(ctx)
    pull(ctx)
    fill(ctx)
    advance(ctx)
    _log.debug("tick end", runner_id=ctx.config.runner_id)
