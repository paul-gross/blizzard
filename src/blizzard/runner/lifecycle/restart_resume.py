"""The Resume dispatch: re-attach to in-flight sessions a restart marked for same-lease resume."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.runner.lifecycle.dormant import DormantContext, DormantSession
from blizzard.runner.lifecycle.model import Fenced, TakeoverHolds
from blizzard.runner.lifecycle.registration import registered_runner_id

# RESUME — the restart re-attach. Its un-recordable middle (a resume whose pid is not yet
# durable) is SPAWN's same by-construction gap; recovery re-runs RESUME idempotently.
_CP_RESUME_BEFORE = crashpoint("resume.before-reattach", "entered RESUME with marked intents; none re-attached yet")


class RestartResumeContext(DormantContext, Protocol):
    """What the Resume dispatch reads: a dormant session's context."""


@dataclass(frozen=True)
class RestartResume:
    ctx: RestartResumeContext

    def run(self) -> None:
        """Re-attach to in-flight sessions a restart marked for same-lease resume — startup recovery.

        Each marked lease is either resumed in place — unchanged ``lease_id``/``epoch``/
        ``session_id``, no retry consumed — or abandoned with no epoch bump. Runs before ADVANCE so a
        resumed lease reads live again by the time ADVANCE iterates."""
        ctx = self.ctx
        intents = ctx.stores.resume_intent.resume_intent_lease_ids()
        if not intents:
            return
        if registered_runner_id(ctx.identity) is None:
            return  # whether each route is still ours waits for the first registration; the intents are durable
        _CP_RESUME_BEFORE.reached()  # marked intents present; a crash here re-runs RESUME unchanged
        active = {lease.lease_id: lease for lease in ctx.stores.lease_record.list_active_leases()}
        takeovers = TakeoverHolds.of(ctx.stores.takeover.open_takeovers())
        for lease_id in intents:
            lease = active.get(lease_id)
            if lease is None:
                ctx.stores.resume_intent.record_resume_clear(lease_id=lease_id, cleared_at=ctx.clock.now())
                continue
            if takeovers.holds_lease(lease):
                continue  # a person holds this session — the intent waits for the takeover to end
            DormantSession(ctx, lease).restart_or_release(Fenced(takeovers))
