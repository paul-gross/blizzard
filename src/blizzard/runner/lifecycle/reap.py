"""The startup recovery sweep for stale and unspawned leases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.foundation.logging import get_logger
from blizzard.runner.leases.activity import Liveness
from blizzard.runner.leases.asks import IWriteAskRepository
from blizzard.runner.leases.liveness import IWriteLeaseLivenessRepository
from blizzard.runner.leases.record import IWriteLeaseRecordRepository
from blizzard.runner.lifecycle.attempt import Attempt, AttemptContext
from blizzard.runner.lifecycle.model import ReapMove, TakeoverHolds, reap_move
from blizzard.runner.lifecycle.registration import registered_runner_id
from blizzard.runner.lifecycle.spawn import Spawner, SpawnStores
from blizzard.runner.lifecycle.takeover import IReadTakeoverRepository

_log = get_logger("blizzard.runner.loop")

_CP_REAP_BEFORE = crashpoint("reap.before-expire", "entered REAP; no lease expired yet")
_CP_REAP_AFTER = crashpoint("reap.after-expire", "REAP done; stale leases expired")


class ReapStores(SpawnStores, Protocol):
    @property
    def asks(self) -> IWriteAskRepository: ...
    @property
    def takeover(self) -> IReadTakeoverRepository: ...
    @property
    def lease_record(self) -> IWriteLeaseRecordRepository: ...
    @property
    def liveness(self) -> IWriteLeaseLivenessRepository: ...


class ReapContext(AttemptContext, Protocol):
    @property
    def stores(self) -> ReapStores: ...


@dataclass(frozen=True)
class ReapSweep:
    ctx: ReapContext

    def run(self) -> None:
        """Expire unspawned or stalled leases after registration."""
        ctx = self.ctx
        if registered_runner_id(ctx.identity) is None:
            # Failing an attempt judges whether its route is still ours — not knowable before the
            # first registration, so every reap waits for it.
            _log.info("reap deferred — not registered at the hub yet", runner_name=ctx.config.runner_name)
            return
        _CP_REAP_BEFORE.reached()
        braked = not Spawner(ctx).brakes().starts_processes
        now = ctx.clock.now()
        parked = ctx.stores.asks.parked_lease_ids()
        takeovers = TakeoverHolds.of(ctx.stores.takeover.open_takeovers())
        active_leases = ctx.stores.lease_record.list_active_leases()
        # One bulk read for every candidate's heartbeat/spawn (`bzh:bulk-reconstitution`).
        liveness_facts = ctx.stores.liveness.liveness_facts([lease.lease_id for lease in active_leases])
        deferred = 0
        for lease in active_leases:
            facts = liveness_facts.get(lease.lease_id)
            liveness = Liveness.of(
                lease,
                heartbeat=facts.latest_heartbeat if facts is not None else None,
                spawn=facts.latest_spawn if facts is not None else None,
            )
            move = reap_move(
                lease,
                taken_over=takeovers.holds_lease(lease),
                parked=lease.lease_id in parked,
                alive=lease.pid is not None and ctx.process.is_alive(lease.pid, lease.process_start_time or ""),
                stale=liveness.stale(now),
                braked=braked,
            )
            if move is ReapMove.REAP_UNSPAWNED:
                if lease.pid is not None:
                    # A durably-provisional generation: close it unidentified before
                    # failing the attempt, so REAP doesn't leave the generation ambiguously open.
                    ctx.stores.liveness.record_identity_failed(lease.lease_id, at=now)
                _log.info("reaping unspawned lease", lease_id=lease.lease_id, chunk_id=lease.chunk_id)
                Attempt(ctx, lease, braked).fail(reason=LeaseClosureReason.REAPED, via="reap")
            elif move is ReapMove.DEFER:
                deferred += 1
            elif move is ReapMove.REAP_STALLED:
                _log.info("reaping stalled worker", lease_id=lease.lease_id, chunk_id=lease.chunk_id, pid=lease.pid)
                Attempt(ctx, lease, braked).fail(reason=LeaseClosureReason.REAPED, via="reap")
        if deferred:
            _log.info("reap deferred — locally paused", runner_name=ctx.config.runner_name, count=deferred)
        _CP_REAP_AFTER.reached()
