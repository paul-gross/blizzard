"""Claiming a chunk's route — off the ready queue, and again after an interrupted claim."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    WorkspaceAcquisitionError,
)
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.spawn import Environments, Spawner
from blizzard.runner.store.repository import EnvBindingRecord
from blizzard.wire.envelope import NodeEnvelope
from blizzard.wire.queue import QueuePeekEntry
from blizzard.wire.route import RouteClaim

_log = get_logger("blizzard.runner.loop")

# The env count a chunk gets when nothing says otherwise — a default, not a structural
# assumption; a chunk holding several is representable.
_DEFAULT_ENV_COUNT = 1

# peek -> acquire -> BIND -> claim -> spawn. The local binding is written *before* the hub
# claim, so a crash in that window is reconciled next tick — never a strand.
_CP_BEFORE_ACQUIRE = crashpoint("fill.before-env-acquire", "peeked a ready chunk; envs not acquired")
_CP_AFTER_ACQUIRE = crashpoint("fill.after-env-acquire.before-bind", "envs acquired; binding not recorded")
_CP_AFTER_BIND = crashpoint("fill.after-bind.before-claim", "binding recorded; route not claimed at the hub")
_CP_AFTER_CLAIM = crashpoint("fill.after-claim.before-spawn", "hub holds the route; lease not minted")


@dataclass(frozen=True)
class ReadyQueue:
    """The hub's ready queue, as the source FILL takes work from — peek the head, acquire its
    environments all-or-nothing, bind them locally, then race for the route."""

    ctx: LoopContext

    def claim_one(self) -> bool:
        """Claim and start one chunk. ``False`` when nothing more can be filled this tick;
        ``True`` when the caller should peek fresh, whether or not this one started."""
        entry = self._peek()
        if entry is None:
            return False
        acquired = self._acquire(entry)
        if acquired is None:
            return False
        chunk_id = entry.chunk_id
        self._bind(chunk_id, acquired)
        try:
            outcome = self.ctx.hub.claim_route(self._route_claim(chunk_id, acquired))
        except HubClientError:
            # Ambiguous — the claim may or may not have committed. Releasing the binding here
            # could strand the chunk, so leave it; the next tick resolves it authoritatively.
            return False
        if outcome.denied_paused is not None:
            # Refused outright, not beaten in the race (issue #44) — stop filling this tick
            # rather than burn the remaining slots on claims that will be refused the same way.
            _log.info(
                "route claim denied — runner paused at the hub", chunk_id=chunk_id, runner_id=self.ctx.config.runner_id
            )
            self.ctx.env_release.release_binding(chunk_id, acquired)
            return False
        if outcome.denied_terminal is not None:
            # The chunk reached a terminal state between this peek and this claim (issue #118)
            # — not a race loss. Undo the binding and move on; it cannot be peeked again.
            _log.info(
                "route claim denied — chunk is terminal", chunk_id=chunk_id, status=outcome.denied_terminal.status
            )
            self.ctx.env_release.release_binding(chunk_id, acquired)
            return True
        if outcome.conflict is not None or outcome.claimed is None:
            _log.info("route claim lost the race", chunk_id=chunk_id)
            self.ctx.env_release.release_binding(chunk_id, acquired)  # someone else won — undo our binding
            return True
        _CP_AFTER_CLAIM.reached()
        # Stash the won claim's plaintext route token (issue #84a) before spawning: every later
        # reader takes it out of the store, never off `outcome.claimed` directly.
        self.ctx.store.set_route_token(chunk_id, token=outcome.claimed.route_token, at=self.ctx.clock.now())
        Spawner(self.ctx).enter_node(chunk_id, outcome.claimed.envelope, acquired, via="fill")
        return True

    def _peek(self) -> QueuePeekEntry | None:
        try:
            peek = self.ctx.hub.peek_queue()
        except HubClientError:
            return None  # hub unreachable — try next tick
        return peek.entries[0] if peek.entries else None

    def _acquire(self, entry: QueuePeekEntry) -> list[AcquiredEnvironment] | None:
        held = self.ctx.store.held_environment_ids()
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
            # `command-failed` (issue #125).
            OutboundFacts(self.ctx).command_failed(
                chunk_id=entry.chunk_id,
                lease_id=None,
                node_name=None,
                command=f"winter env-prep step: {exc.step}",
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
        now = self.ctx.clock.now()
        for env in acquired:
            self.ctx.store.record_binding(
                chunk_id=chunk_id, environment_id=env.environment_id, workdir=env.workdir, bound_at=now
            )
            if self.ctx.events is not None:
                self.ctx.events.publish_environment_changed(chunk_id, env.environment_id, cause="bound")
        _CP_AFTER_BIND.reached()

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
    """Bindings left by a crash in FILL's bind→claim→spawn window.

    The binding is written locally *before* the hub claim, so a crash there leaves a binding
    for a chunk with no active lease. Runs before FILL peeks new work: adopt a route still
    ours, else release the orphaned binding."""

    ctx: LoopContext

    def reconcile(self) -> None:
        requeue_pending = self.ctx.store.pending_requeue_chunk_ids()  # one read per FILL, not per chunk
        for chunk_id in self.ctx.store.live_tenure_chunk_ids():
            if self.ctx.store.active_lease_for_chunk(chunk_id) is None:
                self._reconcile_one(chunk_id, requeued=chunk_id in requeue_pending)
            # else a live worker holds it — REAP/ADVANCE own it

    def _reconcile_one(self, chunk_id: str, *, requeued: bool) -> None:
        try:
            detail = self.ctx.hub.get_chunk(chunk_id)
        except ChunkNotFoundError:
            _log.warning("hub reports interrupted-claim chunk unknown — releasing envs", chunk_id=chunk_id)
            self.ctx.env_release.release_chunk(chunk_id)
            return
        except HubClientError:
            return  # hub unreachable — the binding is durable; retry next tick
        ours = detail.route is not None and detail.route.runner_id == self.ctx.config.runner_id
        if requeued:
            # An explicit human decision (issue #53) outranks every other branch below —
            # nothing here should second-guess it.
            if ours:
                self._resume_requeued(chunk_id)
            else:
                self._release(chunk_id, "releasing binding — chunk requeued locally but no longer routed here")
            return
        if detail.decision is not None:
            # A resolved gate keeps its route live, so it looks exactly like an interrupted
            # claim; without this guard the adopt branch would bump the epoch under the human.
            return
        bindings = self.ctx.store.bindings_for_chunk(chunk_id)
        if not bindings:
            return
        if detail.status == ChunkStatus.RUNNING and ours:
            self._adopt(chunk_id)  # route ours — just spawn the current node
        elif detail.status == ChunkStatus.READY:
            self._reclaim(chunk_id, bindings)  # claim never landed — claim now, reuse the binding
        elif detail.route is not None and not ours:
            self._release(chunk_id, "releasing binding — another runner won the chunk")
        elif detail.route is None:
            # No live route, and neither claimable nor ours to adopt (blizzard#202). Release
            # explicitly instead of matching no branch and leaking the binding forever.
            self._release(
                chunk_id,
                "releasing binding — hub reports no live route in a non-ready, non-running state",
                hub_status=str(detail.status),
            )

    def _adopt(self, chunk_id: str) -> None:
        """Spawn the current node for a claimed chunk whose FILL crashed before the lease minted.

        The route is confirmed and the binding held, but no lease was ever minted, so recovery is
        a spawn of the current node from its idempotent envelope. Also the route-token recovery
        path: the adopted window spans the claim response, so a missing token re-keys here (#84b)."""
        bindings = self.ctx.store.bindings_for_chunk(chunk_id)
        if not bindings:
            _log.warning("adopt with no bound env — cannot spawn", chunk_id=chunk_id)
            return
        if self.ctx.store.route_token(chunk_id) is None:
            try:
                rekeyed = self.ctx.hub.rekey_route_token(chunk_id)
            except ChunkNotFoundError:
                self._unknown(chunk_id, "adopted")
                return
            except HubClientError:
                return  # hub unreachable — the binding is durable; retry next tick
            self.ctx.store.set_route_token(chunk_id, token=rekeyed.route_token, at=self.ctx.clock.now())
        envelope = self._envelope(chunk_id, "adopted")
        if envelope is None:
            return
        _log.info("adopting interrupted claim — spawning current node", chunk_id=chunk_id)
        Spawner(self.ctx).spawn(chunk_id, envelope, Environments(bindings).acquired, via="adopt")

    def _resume_requeued(self, chunk_id: str) -> None:
        """Spawn a fresh attempt at the chunk's current node — its local hold is cleared (#53).

        The hold-clearing fact is already durable when this runs (``bzh:crash-correctness``). The
        retry budget is **carried, not reset** — an ordinary mint against the node's existing
        ``retries_max``, so a requeue buys exactly one more try."""
        bindings = self.ctx.store.bindings_for_chunk(chunk_id)
        if not bindings:
            _log.warning("requeue-resume with no bound env — cannot spawn", chunk_id=chunk_id)
            return
        envelope = self._envelope(chunk_id, "requeued")
        if envelope is None:
            return
        _log.info("resuming requeued chunk — spawning current node", chunk_id=chunk_id)
        Spawner(self.ctx).spawn(chunk_id, envelope, Environments(bindings).acquired, via="requeue-resume")

    def _reclaim(self, chunk_id: str, bindings: list[EnvBindingRecord]) -> None:
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
        if outcome.denied_paused is not None:
            # Refused outright because this runner is paused upstream, not lost to another
            # runner (issue #44).
            self._release(chunk_id, "interrupted claim denied — runner paused at the hub")
            return
        if outcome.conflict is not None or outcome.claimed is None:
            self._release(chunk_id, "interrupted claim lost the race — releasing binding")
            return
        _log.info("re-claimed interrupted chunk — spawning current node", chunk_id=chunk_id)
        # A reclaim is a fresh claim, so its token overwrites whatever this chunk's row held
        # before — a fresh claim always wins (issue #84a).
        self.ctx.store.set_route_token(chunk_id, token=outcome.claimed.route_token, at=self.ctx.clock.now())
        Spawner(self.ctx).spawn(chunk_id, outcome.claimed.envelope, envs, via="reclaim")

    def _envelope(self, chunk_id: str, what: str) -> NodeEnvelope | None:
        try:
            return self.ctx.hub.get_envelope(chunk_id)
        except ChunkNotFoundError:
            self._unknown(chunk_id, what)
            return None
        except HubClientError:
            return None  # hub unreachable — the binding is durable; retry next tick

    def _unknown(self, chunk_id: str, what: str) -> None:
        _log.warning(f"hub reports {what} chunk unknown — releasing envs", chunk_id=chunk_id)
        self.ctx.env_release.release_chunk(chunk_id)

    def _release(self, chunk_id: str, message: str, **fields: object) -> None:
        _log.info(message, chunk_id=chunk_id, **fields)
        self.ctx.env_release.release_chunk(chunk_id)
