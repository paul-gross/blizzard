"""The ownership reconcile: bring the runner's lease, escalation and takeover facts into line with the hub's view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.hub.chunk_status_cache import IChunkViews
from blizzard.runner.hub.client import ChunkNotFoundError, HubClientError
from blizzard.runner.hub.identity import RunnerIdentityHolder
from blizzard.runner.leases.escalations import IWriteEscalationRepository
from blizzard.runner.lifecycle.attempt import Attempt, AttemptContext
from blizzard.runner.lifecycle.model import Fenced, LeaseReconcileMove, TakeoverHolds, lease_reconcile_move
from blizzard.runner.lifecycle.registration import registered_runner_id
from blizzard.runner.lifecycle.spawn import SpawnStores
from blizzard.runner.lifecycle.takeover import IWriteTakeoverRepository, TakeoverCloser, TakeoverCloseScope

_log = get_logger("blizzard.runner.loop")


class OwnershipReconcileStores(SpawnStores, Protocol):
    """The repositories the reconcile reads and writes itself, each at the type its base already declares."""

    @property
    def takeover(self) -> IWriteTakeoverRepository: ...
    @property
    def escalations(self) -> IWriteEscalationRepository: ...


class OwnershipReconcileContext(AttemptContext, Protocol):
    """What the ownership reconcile reads: an attempt's context, and the stores it reads itself."""

    @property
    def stores(self) -> OwnershipReconcileStores: ...
    @property
    def chunk_views(self) -> IChunkViews: ...
    @property
    def identity(self) -> RunnerIdentityHolder: ...
    @property
    def clock(self) -> IClock: ...
    @property
    def events(self) -> IRunnerEventPublisher | None: ...


@dataclass(frozen=True)
class OwnershipReconcile:
    ctx: OwnershipReconcileContext

    def run(self) -> None:
        """Reconcile leases, then escalations, then takeovers against the hub's per-chunk views."""
        self._reconcile_leases()
        self._reconcile_escalations()
        self._reconcile_takeovers()

    def _reconcile_leases(self) -> None:
        """Reconcile every active lease against its chunk's view (``lease_reconcile_move``), from
        one ``ctx.chunk_views.get`` per lease. A transport failure leaves the lease as it is."""
        ctx = self.ctx
        runner_id = registered_runner_id(ctx.identity)
        if runner_id is None:
            return  # no route can be judged ours before the first registration — every lease holds
        pause_parked = ctx.stores.pause.pause_parked_lease_ids()  # hoisted: the park guard, one read per tick
        fenced = Fenced(TakeoverHolds.of(ctx.stores.takeover.open_takeovers()))
        for lease in ctx.stores.lease_record.list_active_leases():
            try:
                view = ctx.chunk_views.get(lease.chunk_id)
            except ChunkNotFoundError:
                # Terminal, not retryable. Ordered before the HubClientError arm
                # because it subclasses it, or the 404 would be swallowed as "hub unreachable".
                Attempt(ctx, lease).abandon(via="pull")
                continue
            except HubClientError:
                continue  # hub unreachable — last-known directive holds; keep working
            move = lease_reconcile_move(
                view,
                lease,
                runner_id=runner_id,
                pause_parked=pause_parked,
                fenced=fenced.out(view, lease),
            )
            if move is LeaseReconcileMove.ABANDON:
                Attempt(ctx, lease).abandon(via="pull")
            elif move is LeaseReconcileMove.PARK:
                Attempt(ctx, lease).park_paused(via="pull")
            elif move is LeaseReconcileMove.PREEMPT:
                Attempt(ctx, lease).preempt(via="pull")

    def _reconcile_escalations(self) -> None:
        """Close every open local escalation its chunk's view supersedes, from one
        ``ctx.chunk_views.get`` each; an unreadable chunk leaves it open. Supersession is owned by
        ``blizzard-context:/domain/humans/escalation.md`` §Supersession."""
        ctx = self.ctx
        runner_id = registered_runner_id(ctx.identity)
        if runner_id is None:
            return  # supersession reads the route's holder — every escalation stays open until the first registration
        fenced = Fenced(TakeoverHolds.of(ctx.stores.takeover.open_takeovers()))
        for escalation in ctx.stores.escalations.open_escalations():
            try:
                view = ctx.chunk_views.get(escalation.chunk_id)
            except HubClientError as exc:
                # Covers ChunkNotFoundError: an unknown chunk is not a resolution.
                _log.debug("escalation left open — hub unreadable", chunk_id=escalation.chunk_id, error=str(exc))
                continue
            superseded = escalation.superseded_by(view, runner_id=runner_id, fenced_out=fenced.out(view, escalation))
            if not superseded:
                _log.debug("escalation left open", chunk_id=escalation.chunk_id, hub_status=str(view.status))
                continue
            ctx.stores.escalations.record_escalation_closure(
                chunk_id=escalation.chunk_id, reason=str(view.status), at=ctx.clock.now()
            )
            if ctx.events is not None:
                ctx.events.publish_escalation_changed(
                    escalation.chunk_id,
                    cause="closed",
                    lease_id=escalation.lease_id,
                )

    def _reconcile_takeovers(self) -> None:
        """Close an open takeover whose chunk the hub has ended — one
        ``ctx.chunk_views.get`` each, the same per-tick cache read the other two reconcile
        sweeps make. The takeover fact now authorizes the resumed session's
        worker verbs, so a chunk the hub ends mid-takeover must not leave that
        authorization standing forever; this is the second, no-person-drives closer alongside
        the CLI's own end-PATCH. The mark is what keeps the read hub-free (``bzh:facts-not-status``)."""
        ctx = self.ctx
        for takeover in ctx.stores.takeover.open_takeovers():
            try:
                view = ctx.chunk_views.get(takeover.chunk_id)
            except HubClientError as exc:
                # Covers ChunkNotFoundError: an unknown chunk is not a resolution.
                _log.debug("takeover left open — hub unreadable", chunk_id=takeover.chunk_id, error=str(exc))
                continue
            if not takeover.ended_by(view):
                _log.debug("takeover left open", chunk_id=takeover.chunk_id, hub_status=str(view.status))
                continue
            TakeoverCloser(ctx.stores.takeover, ctx.clock, ctx.events).close(
                TakeoverCloseScope(chunk_id=takeover.chunk_id, open_takeover=takeover), takeover.takeover_id
            )
