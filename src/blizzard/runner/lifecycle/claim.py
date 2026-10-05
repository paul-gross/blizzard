"""Claiming a chunk's route — off the ready queue, and again after an interrupted claim."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.trace_ids import StepKey, step_root
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    IWorkspaceProvider,
    WorkspaceAcquisitionError,
)
from blizzard.runner.environments.repository import (
    EnvBinding,
    EnvironmentHeldError,
    IWriteEnvironmentRepository,
    group_bindings_by_chunk,
    require_unheld,
)
from blizzard.runner.hub.client import ChunkEndedError, ChunkNotFoundError, HubClientError
from blizzard.runner.hub.outbound import OutboundFacts
from blizzard.runner.leases.operator_requests import IReadRequeueRepository
from blizzard.runner.lifecycle.model import (
    ClaimVerdict,
    InterruptedClaimMove,
    adopt_enters_node,
    claim_denial_fields,
    claim_disposition,
    claim_verdict,
    interrupted_claim_move,
    owns_node_entry,
    pick_claim_entry,
    reclaim_verdict,
    recovery_owner,
)
from blizzard.runner.lifecycle.spawn import Environments, SpawnConfig, SpawnContext, Spawner, SpawnStores
from blizzard.wire.chunk import ChunkStatusView
from blizzard.wire.envelope import NodeEnvelope
from blizzard.wire.queue import QueuePeekEntry, QueuePeekRequest
from blizzard.wire.route import RouteClaim
from blizzard.wire.runner import RunnerCapability

_log = get_logger("blizzard.runner.loop")

# Default count, not a limit on how many environments a chunk may hold.
_DEFAULT_ENV_COUNT = 1

# Bind before claiming so the next tick can reconcile a crash in that window.
_CP_BEFORE_ACQUIRE = crashpoint("fill.before-env-acquire", "peeked a ready chunk; envs not acquired")
_CP_AFTER_ACQUIRE = crashpoint("fill.after-env-acquire.before-bind", "envs acquired; binding not recorded")
_CP_AFTER_BIND = crashpoint("fill.after-bind.before-claim", "binding recorded; route not claimed at the hub")
_CP_AFTER_CLAIM = crashpoint("fill.after-claim.before-spawn", "hub holds the route; lease not minted")

#: What each lost claim logs before its binding is released.
_CLAIM_LOSS_LOG: dict[ClaimVerdict, str] = {
    ClaimVerdict.PAUSED: "route claim denied — the hub refused this runner",
    ClaimVerdict.NOT_CLAIMABLE: "route claim denied — chunk not claimable",
    ClaimVerdict.DEPENDENCY: "route claim denied — unmet prerequisite",
    ClaimVerdict.INCOMPATIBLE: "route claim denied — runner incompatible with chunk",
    ClaimVerdict.LOST: "route claim lost the race",
}


class ClaimStores(SpawnStores, Protocol):
    @property
    def environments(self) -> IWriteEnvironmentRepository: ...
    @property
    def requeue(self) -> IReadRequeueRepository: ...


class ClaimConfig(SpawnConfig, Protocol):
    @property
    def queue_strict(self) -> bool: ...


class ClaimContext(SpawnContext, Protocol):
    @property
    def stores(self) -> ClaimStores: ...
    @property
    def config(self) -> ClaimConfig: ...
    @property
    def provider(self) -> IWorkspaceProvider: ...
    def capability_snapshot(self) -> tuple[RunnerCapability, ...]: ...


@dataclass
class ReadyQueue:
    """The hub's ready queue, as the source FILL takes work from — peek the head, acquire its
    environments all-or-nothing, bind them locally, then race for the route. ``_entries`` is
    one peek's own snapshot, holding at most one entry when a capability-asserting runner
    peeks fresh before every ``claim_one()`` (``tests/test_runner_loop.py``'s pinning)."""

    ctx: ClaimContext
    _entries: list[QueuePeekEntry] = field(default_factory=list)

    @classmethod
    def peeked(cls, ctx: ClaimContext) -> ReadyQueue:
        request = QueuePeekRequest(
            capabilities=list(ctx.capability_snapshot()),
            # The same knob `_next` reach-ahead already honors locally, sent per call
            # rather than read hub-side, so both dimensions get the identical policy.
            policy="hold" if ctx.config.queue_strict else "pass-over",
        )
        try:
            peeked = ctx.hub.peek_queue(request)
        except HubClientError:
            return cls(ctx, _entries=[])
        return cls(ctx, _entries=list(peeked.entries))

    def claim_one(self) -> bool:
        """Claim and start one chunk. ``False`` when nothing more can be filled this tick;
        ``True`` when the caller should peek fresh, whether or not this one started."""
        entry = self._next()
        if entry is None:
            return False
        acquired = self._acquire(entry)
        if acquired is None:
            return False
        chunk_id = entry.chunk_id
        try:
            self._bind(chunk_id, acquired)
        except EnvironmentHeldError as exc:
            self._refuse_held(entry, acquired, exc)
            return False
        try:
            outcome = self.ctx.hub.claim_route(self._route_claim(chunk_id, acquired))
        except HubClientError:
            # Ambiguous — the claim may or may not have committed. Releasing the binding here
            # could strand the chunk, so leave it; the next tick resolves it authoritatively.
            return False
        if outcome.won:
            self.ctx.chunk_views.invalidate(chunk_id)  # a later get() this tick sees the win
        disposition = claim_disposition(claim_verdict(outcome), strict=self.ctx.config.queue_strict)
        if disposition.drop_entry:
            self._entries.remove(entry)
        if disposition.release:
            _log.info(
                _CLAIM_LOSS_LOG[disposition.verdict],
                chunk_id=chunk_id,
                runner_id=self.ctx.config.runner_id,
                **claim_denial_fields(outcome),
            )
            self.ctx.env_release.release_binding(chunk_id, acquired)
            return disposition.keep_filling
        assert outcome.claimed is not None  # a won claim carries its route
        _CP_AFTER_CLAIM.reached()
        # Stash the won claim's plaintext route token before spawning: every later
        # reader takes it out of the store, never off `outcome.claimed` directly.
        self.ctx.stores.tokens.set_route_token(chunk_id, token=outcome.claimed.route_token, at=self.ctx.clock.now())
        Spawner(self.ctx).enter_node(chunk_id, outcome.claimed.envelope, acquired, via="fill")
        return True

    def _next(self) -> QueuePeekEntry | None:
        """Pick this runner's entry out of this fill's one peeked snapshot,
        left in place until ``claim_one()`` knows the outcome and drops it itself —
        a later ``claim_one()`` this same ``Fill.run()`` must not silently move past an
        entry whose outcome is still undetermined. Strict holds at a marked head;
        reach-ahead scans for the first unmarked entry."""
        return pick_claim_entry(self._entries, strict=self.ctx.config.queue_strict)

    def _acquire(self, entry: QueuePeekEntry) -> list[AcquiredEnvironment] | None:
        held = self.ctx.stores.environments.held_environment_ids()
        _CP_BEFORE_ACQUIRE.reached()
        try:
            return self.ctx.provider.acquire(entry.chunk_id, self._environments_wanted(entry), held)
        except EnvironmentPreparationError as exc:
            # Not capacity — a reset-on-acquire step failed. The provider aborted rather than
            # hand over a half-reset env, so the chunk waits for a fixed workspace.
            _log.error(
                "environment preparation failed at FILL",
                chunk_id=entry.chunk_id,
                environment_id=exc.environment_id,
                step=exc.step,
                detail=str(exc),
            )
            # No lease exists yet (the chunk is not claimed), so this is a chunk-scoped
            # `command-failed`.
            OutboundFacts(self.ctx).command_failed(
                chunk_id=entry.chunk_id,
                lease_id=None,
                node_name=None,
                command=f"environment preparation step: {exc.step}",
                stderr_tail=str(exc),
            )
            return None
        except WorkspaceAcquisitionError:
            _log.info("acquire refused — env-bound this tick", chunk_id=entry.chunk_id)
            return None  # env capacity exhausted; the chunk waits

    def _bind(self, chunk_id: str, acquired: list[AcquiredEnvironment]) -> None:
        """Bind locally BEFORE claiming at the hub: without a local trace, a crash after a won
        claim would strand the chunk with nothing on this side to drive or reap."""
        _CP_AFTER_ACQUIRE.reached()
        require_unheld(chunk_id, [env.environment_id for env in acquired], self.ctx.stores.environments.held_bindings())
        now = self.ctx.clock.now()
        for env in acquired:
            self.ctx.stores.environments.record_binding(
                chunk_id=chunk_id, environment_id=env.environment_id, workdir=env.workdir, bound_at=now
            )
            if self.ctx.events is not None:
                self.ctx.events.publish_environment_changed(chunk_id, env.environment_id, cause="bound")
        _CP_AFTER_BIND.reached()

    def _refuse_held(
        self, entry: QueuePeekEntry, acquired: list[AcquiredEnvironment], exc: EnvironmentHeldError
    ) -> None:
        """The provider handed back an environment another chunk still holds: record no binding,
        give back every acquired environment no other chunk holds, and drop the entry so the rest
        of the tick still runs. A held environment stays with its holder — releasing it at the
        provider would free it under that chunk."""
        _log.error(
            "acquired an environment another chunk holds — binding refused",
            chunk_id=entry.chunk_id,
            environment_id=exc.environment_id,
            holder_chunk_id=exc.holder_chunk_id,
        )
        held_by_others = {
            binding.environment_id
            for binding in self.ctx.stores.environments.held_bindings()
            if binding.chunk_id != entry.chunk_id
        }
        for env in acquired:
            if env.environment_id not in held_by_others:
                self.ctx.provider.release(env.environment_id)
        self._entries.remove(entry)

    def _route_claim(self, chunk_id: str, acquired: list[AcquiredEnvironment]) -> RouteClaim:
        return RouteClaim(
            chunk_id=chunk_id,
            runner_id=self.ctx.config.runner_id,
            workspace_id=self.ctx.config.workspace_id,
            environment_ids=[env.environment_id for env in acquired],
        )

    def _environments_wanted(self, entry: QueuePeekEntry) -> int:
        """How many environments this queue entry's chunk should be acquired.

        The single place the count is decided, so raising it above one is a change here rather
        than an audit of everything that assumed a lone binding."""
        del entry  # no per-chunk demand signal exists yet
        return _DEFAULT_ENV_COUNT


@dataclass(frozen=True)
class InterruptedClaims:
    """Reconcile bindings left in FILL's bind→claim→spawn window.

    Before FILL peeks new work, recover a node entry ADVANCE will not make, or release
    an orphan. A strictly newer hub epoch belongs to ADVANCE."""

    ctx: ClaimContext

    def reconcile(self, *, braked: bool = False) -> None:
        """``braked`` — either pause brake is engaged: the reclaim arm, the only one that makes a
        new hub claim, keeps its binding instead of claiming; every other arm still runs.

        Open takeovers do not suppress this reconciliation."""
        requeue_pending = self.ctx.stores.requeue.pending_requeue_chunk_ids()  # one read per FILL, not per chunk
        # One read before the loop, not one `active_lease_for_chunk` per chunk
        # (`bzh:bulk-reconstitution`) — safe because each iteration only mutates its own chunk.
        active_chunk_ids = {lease.chunk_id for lease in self.ctx.stores.lease_record.list_active_leases()}
        # Likewise one read for every held binding, grouped by chunk, not one per held chunk.
        bindings_by_chunk = group_bindings_by_chunk(self.ctx.stores.environments.held_bindings())
        for chunk_id, bindings in bindings_by_chunk.items():
            if chunk_id not in active_chunk_ids:
                self._reconcile_one(chunk_id, bindings, requeued=chunk_id in requeue_pending, braked=braked)
            # else a live worker holds it — REAP/ADVANCE own it

    def _reconcile_one(self, chunk_id: str, bindings: list[EnvBinding], *, requeued: bool, braked: bool) -> None:
        try:
            view = self.ctx.chunk_views.get(chunk_id)
        except ChunkNotFoundError:
            _log.warning("hub reports interrupted-claim chunk unknown — releasing envs", chunk_id=chunk_id)
            self.ctx.env_release.release_chunk(chunk_id)
            return
        except HubClientError:
            return  # hub unreachable — the binding is durable; retry next tick
        move = interrupted_claim_move(view, runner_id=self.ctx.config.runner_id, requeued=requeued, braked=braked)
        if move is InterruptedClaimMove.RESUME_REQUEUED:
            self._resume_requeued(chunk_id, view.latest_epoch)
        elif move is InterruptedClaimMove.ADOPT:
            if self._owns_node_entry(chunk_id, view, bindings):
                self._adopt(chunk_id, view)
        elif move is InterruptedClaimMove.RECLAIM:
            self._reclaim(chunk_id, bindings)  # claim never landed — claim now, reuse the binding
        elif move is InterruptedClaimMove.RELEASE_REQUEUED_ELSEWHERE:
            self._release(chunk_id, "releasing binding — chunk requeued locally but no longer routed here")
        elif move is InterruptedClaimMove.RELEASE_OTHER_RUNNER:
            self._release(chunk_id, "releasing binding — another runner won the chunk")
        elif move is InterruptedClaimMove.RELEASE_NO_ROUTE:
            self._release(
                chunk_id,
                "releasing binding — hub reports no live route in a non-ready, non-running state",
                hub_status=str(view.status),
            )

    def _owns_node_entry(self, chunk_id: str, view: ChunkStatusView, bindings: list[EnvBinding]) -> bool:
        """Whether FILL, not ADVANCE, spawns this running chunk's lease-less current node.

        ADVANCE enters a strictly newer hub epoch through the node's declared session, so
        FILL keeps the runner's own epoch (a suppressed or interrupted respawn), the current
        restart entry, and a first claim with no lease in this binding tenure."""
        open_escalation = self.ctx.stores.escalations.open_escalation_for_chunk(chunk_id)
        bound_at = min(binding.bound_at for binding in bindings)
        return owns_node_entry(
            view,
            local_epoch=self.ctx.stores.lease_record.latest_epoch(chunk_id),
            open_escalation_epoch=open_escalation.epoch if open_escalation is not None else None,
            lease_in_binding_tenure=self.ctx.stores.lease_record.has_lease_in_binding_tenure(chunk_id, bound_at),
        )

    def _adopt(self, chunk_id: str, view: ChunkStatusView) -> None:
        """Spawn the current node for a claimed chunk whose spawn never minted a lease.

        The route is confirmed and the binding held, but no lease was ever minted, so recovery is
        a spawn of the current node from its idempotent envelope. Also the route-token recovery
        path: the adopted window spans the claim response, so a missing token re-keys here (#84b)."""
        bindings = self.ctx.stores.environments.bindings_for_chunk(chunk_id)
        if not bindings:
            _log.warning("adopt with no bound env — cannot spawn", chunk_id=chunk_id)
            return
        if self.ctx.stores.tokens.route_token(chunk_id) is None:
            try:
                rekeyed = self.ctx.hub.rekey_route_token(chunk_id)
            except (ChunkNotFoundError, ChunkEndedError):
                self._unknown(chunk_id, "adopted")
                return
            except HubClientError:
                return  # hub unreachable — the binding is durable; retry next tick
            self.ctx.chunk_views.invalidate(chunk_id)  # named alongside the other writes
            self.ctx.stores.tokens.set_route_token(chunk_id, token=rekeyed.route_token, at=self.ctx.clock.now())
        envelope = self._envelope(chunk_id, "adopted", view.latest_epoch)
        if envelope is None:
            return
        latest = self.ctx.stores.lease_record.latest_lease_for_chunk(chunk_id)
        acquired = Environments(bindings).acquired
        if adopt_enters_node(latest, view, envelope.node.node_id):
            _log.info("adopting held claim — entering its current node", chunk_id=chunk_id)
            Spawner(self.ctx).enter_node(chunk_id, envelope, acquired, via="adopt")
            return
        _log.info("adopting interrupted claim — spawning current node", chunk_id=chunk_id)
        Spawner(self.ctx).spawn(chunk_id, envelope, acquired, via="adopt", harness_id=recovery_owner(latest))

    def _resume_requeued(self, chunk_id: str, latest_epoch: int | None) -> None:
        """Spawn a fresh attempt at the chunk's current node — its local hold is cleared (#53).

        The hold-clearing fact is already durable when this runs (``bzh:crash-correctness``). The
        retry budget is **carried, not reset** — an ordinary mint against the node's existing
        ``retries_max``, so a requeue buys exactly one more try."""
        bindings = self.ctx.stores.environments.bindings_for_chunk(chunk_id)
        if not bindings:
            _log.warning("requeue-resume with no bound env — cannot spawn", chunk_id=chunk_id)
            return
        envelope = self._envelope(chunk_id, "requeued", latest_epoch)
        if envelope is None:
            return
        _log.info("resuming requeued chunk — spawning current node", chunk_id=chunk_id)
        Spawner(self.ctx).spawn(
            chunk_id,
            envelope,
            Environments(bindings).acquired,
            via="requeue-resume",
            harness_id=self._latest_owner(chunk_id),
        )

    def _reclaim(self, chunk_id: str, bindings: list[EnvBinding]) -> None:
        """Complete a claim whose hub POST never landed — claim now, reusing the held binding.

        The environment was bound but the claim never landed, so the chunk still reads ``ready``.
        The route is claimed with the environment already held rather than re-acquired; a lost
        race releases the binding."""
        envs = Environments(bindings).acquired
        claim = RouteClaim(
            chunk_id=chunk_id,
            runner_id=self.ctx.config.runner_id,
            workspace_id=self.ctx.config.workspace_id,
            environment_ids=[b.environment_id for b in bindings],
        )
        try:
            outcome = self.ctx.hub.claim_route(claim)
        except HubClientError:
            return  # hub unreachable — the binding is durable; retry next tick
        if outcome.won:
            self.ctx.chunk_views.invalidate(chunk_id)  # a later get() this tick sees the win
        verdict = reclaim_verdict(outcome)
        if verdict is ClaimVerdict.PAUSED:
            # Refused outright because the hub refuses this runner, not lost to another runner.
            self._release(
                chunk_id, "interrupted claim denied — the hub refused this runner", **claim_denial_fields(outcome)
            )
            return
        if verdict is ClaimVerdict.LOST or outcome.claimed is None:
            self._release(chunk_id, "interrupted claim not won — releasing binding", **claim_denial_fields(outcome))
            return
        _log.info("re-claimed interrupted chunk — spawning current node", chunk_id=chunk_id)
        # A reclaim is a fresh claim, so its token overwrites whatever this chunk's row held
        # before — a fresh claim always wins.
        self.ctx.stores.tokens.set_route_token(chunk_id, token=outcome.claimed.route_token, at=self.ctx.clock.now())
        Spawner(self.ctx).spawn(
            chunk_id, outcome.claimed.envelope, envs, via="reclaim", harness_id=self._latest_owner(chunk_id)
        )

    def _latest_owner(self, chunk_id: str) -> str | None:
        """The chunk's most recently minted lease's own owner, if it has one yet — carried
        into a recovery spawn so it never falls back to the default harness under a chunk
        this runner already minted under a different owner. ``None`` for a chunk with no
        prior mint, the genuinely fresh case a caller's own default is free to decide."""
        return recovery_owner(self.ctx.stores.lease_record.latest_lease_for_chunk(chunk_id))

    def _envelope(self, chunk_id: str, what: str, latest_epoch: int | None) -> NodeEnvelope | None:
        try:
            with self._under_latest_step(chunk_id, latest_epoch):
                return self.ctx.hub.get_envelope(chunk_id)
        except (ChunkNotFoundError, ChunkEndedError):
            self._unknown(chunk_id, what)
            return None
        except HubClientError:
            return None  # hub unreachable — the binding is durable; retry next tick

    def _under_latest_step(self, chunk_id: str, latest_epoch: int | None) -> AbstractContextManager[None]:
        epoch = latest_epoch or self.ctx.stores.lease_record.latest_epoch(chunk_id)
        if not epoch:
            return nullcontext()
        return self.ctx.tracer.under(step_root(StepKey.attempt(chunk_id, epoch)))

    def _unknown(self, chunk_id: str, what: str) -> None:
        _log.warning("hub reports chunk unknown or ended — releasing envs", what=what, chunk_id=chunk_id)
        self.ctx.env_release.release_chunk(chunk_id)

    def _release(self, chunk_id: str, message: str, **fields: object) -> None:
        _log.info(message, chunk_id=chunk_id, **fields)
        self.ctx.env_release.release_chunk(chunk_id)
