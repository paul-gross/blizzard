"""The reconciliation steps — REAP → PULL → FILL → ADVANCE (``bzh:steppable-loop``).

Each is an individually runnable :class:`Step` over a :class:`LoopContext`. Every step is
idempotent and holds no state of its own — all facts live in the runner store, so a crash
mid-tick and a restart re-run the tick harmlessly; startup recovery is REAP running first.
"""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.lifecycle.advance import AdvanceSweep
from blizzard.runner.lifecycle.claim import InterruptedClaims
from blizzard.runner.lifecycle.context_sample import ContextSampleSweep
from blizzard.runner.lifecycle.drain import OutboundDrain
from blizzard.runner.lifecycle.fill import BindingFill
from blizzard.runner.lifecycle.held_binding_completion import HeldBindingCompletion
from blizzard.runner.lifecycle.ownership_reconcile import OwnershipReconcile
from blizzard.runner.lifecycle.reap import ReapSweep
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.lifecycle.restart_resume import RestartResume
from blizzard.runner.lifecycle.retention import RetentionPrune
from blizzard.runner.lifecycle.spawn import Spawner
from blizzard.runner.lifecycle.spend_ceiling import SpendCeilingCheck
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.throttle.gate import ThrottleGate
from blizzard.runner.throttle.levers import LeverAnswer
from blizzard.runner.throttle.report import FLOOR_LEVER

#: This module's public API — the loop steps it owns, in tick order.
__all__ = [
    "Advance",
    "CompleteHeldBindings",
    "ContextSample",
    "Fill",
    "Pull",
    "Reap",
    "Resume",
    "Retention",
    "SpendCeiling",
    "Step",
]

_log = get_logger("blizzard.runner.loop")

# The env count a chunk gets when nothing says otherwise.
_DEFAULT_ENV_COUNT = 1

# Crash points (``bzh:crash-point-registry``): armed, each SIGKILLs the tick subprocess at
# the boundary it guards; unarmed, a module-global string compare and a no-op.

# PULL — the single outbound flusher (store-and-forward drain).
_CP_PULL_BEFORE = crashpoint(
    "pull.before-flush", "entered PULL; registry synced, leases and escalations reconciled, buffer not drained"
)
_CP_PULL_AFTER = crashpoint("pull.after-flush", "PULL done; buffer drained as far as it could")


@dataclass(frozen=True)
class Step:
    """One reconciliation step over a tick's context — individually runnable, so a test or
    a CLI verb can drive exactly one (``bzh:steppable-loop``)."""

    ctx: LoopContext

    def run(self) -> None:
        raise NotImplementedError


class SpendCeiling(Step):
    """The tick-level kill-switch — first in the tick."""

    def run(self) -> None:
        """Engage the local pause brake once spend reaches the ceiling; runs **first** in the tick so a
        crossing is visible to every later step in the same pass."""
        SpendCeilingCheck(self.ctx).run()


class Reap(Step):
    def run(self) -> None:
        """Expire leases whose worker is gone or **stalled** — each a failed attempt.

        An **orphan** (minted but never spawned) and a **stalled-but-alive** worker end here.
        An exited session-bearing worker is ADVANCE's: exit is the done declaration, and the
        conservative staleness threshold is what keeps the two apart."""
        ReapSweep(self.ctx).run()


class Resume(Step):
    def run(self) -> None:
        """Re-attach to in-flight sessions a restart marked for same-lease resume — startup recovery."""
        RestartResume(self.ctx).run()


class Pull(Step):
    def run(self) -> None:
        """Exchange facts with the hub: sync the registry, reconcile ownership, drain the buffer.

        Reconciliation runs BEFORE the drain, so a preempted lease's queued submission still
        reaches the hub and the drain absorbs the stale-epoch rejection against a lease already
        closed — the retry budget the move must not spend is never reached."""
        self._sync_registry()
        OwnershipReconcile(self.ctx).run()
        _CP_PULL_BEFORE.reached()
        OutboundDrain(self.ctx).run()
        _CP_PULL_AFTER.reached()

    def _sync_registry(self) -> None:
        """``src/blizzard/runner/lifecycle/registration.py``'s ``Registration``."""
        Registration(self.ctx).run()


class CompleteHeldBindings(Step):
    def run(self) -> None:
        HeldBindingCompletion(self.ctx).complete()


class Fill(Step):
    def run(self) -> None:
        """Keep the fleet busy: peek → acquire → claim-by-route → bind → spawn.

        Each binding's open slots are its ``agents`` less the open leases counted against it; for each,
        peek the binding's project, acquire the chunk's environments all-or-nothing from the binding's
        workspace, and claim the route. Either brake, or the throttle gate declining, stops *new* claims.
        """
        ctx = self.ctx
        brakes = Spawner(ctx).brakes()
        claims = ThrottleGate(ctx).claims(brakes)
        InterruptedClaims(ctx).reconcile(braked=claims.held_back)
        if claims.braked:
            _log.info(
                "paused — no new claims this tick",
                runner_name=ctx.config.runner_name,
                hub_paused=brakes.hub,
                local_paused=brakes.local,
            )
            return
        declining = claims.declining
        if declining is not None:
            hold = declining.floor.hold
            _log.info(
                "declining — no new claims this tick",
                runner_name=ctx.config.runner_name,
                declining=[
                    answer.lever.value if isinstance(answer, LeverAnswer) else FLOOR_LEVER
                    for answer in declining.declining
                ],
                subscription=declining.floor.slug,
                hold=hold.kind if hold is not None else None,
                expected_reset=iso_utc(hold.expected_reset) if hold is not None else None,
            )
            return
        BindingFill(ctx).fill()


class Advance(Step):
    def run(self) -> None:
        AdvanceSweep(self.ctx).run()


class Retention(Step):
    """Prune every append-only observation/report lane once per floor — owned by
    :class:`~blizzard.runner.lifecycle.retention.RetentionPrune`."""

    def run(self) -> None:
        RetentionPrune(self.ctx).run()


class ContextSample(Step):
    """Every running lease's live session context, sampled and warned on — never enforced.

    Owned by :class:`~blizzard.runner.lifecycle.context_sample.ContextSampleSweep`."""

    def run(self) -> None:
        ContextSampleSweep(self.ctx).run()
