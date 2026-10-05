"""A chunk whose environments this runner holds, and the moves that keep it going."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.foundation.node_steps import ApplyOutcome
from blizzard.foundation.trace_ids import StepKey, step_root
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.hub.client import ChunkEndedError, ChunkNotFoundError, HubClientError
from blizzard.runner.lifecycle.model import (
    ApplyMove,
    HeldChunkMove,
    TakeoverHolds,
    apply_move,
    chunk_paused,
    gate_resolution_lease_id,
    held_chunk_move,
    held_chunk_reads_local_epoch,
)
from blizzard.runner.lifecycle.spawn import Environments, SpawnContext, Spawner
from blizzard.runner.node_steps.chunk_state import ChunkGate
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.node_steps.submissions import Completion

_log = get_logger("blizzard.runner.loop")


class HeldChunkContext(SpawnContext, Protocol): ...


@dataclass(frozen=True)
class HeldChunk:
    """A chunk this runner holds environments for, driven one move at a time — by the hub's
    answer to an applied step, or by polling when no lease is active."""

    ctx: HeldChunkContext
    chunk_id: str

    def apply(self, outcome: ApplyOutcome, next_envelope: Envelope | None, bindings: list[EnvBinding]) -> None:
        # Only a next node can be spawned into a pause, so only then is the chunk's pause read.
        move = apply_move(outcome, next_envelope, chunk_paused=next_envelope is not None and self._chunk_paused())
        if move is ApplyMove.ENTER_NEXT and next_envelope is not None:
            Spawner(self.ctx).enter_node(
                self.chunk_id, next_envelope, Environments(bindings).acquired, via="apply-response"
            )
        elif move is ApplyMove.HOLD_PAUSED:
            # The binding is held; once the pause lifts, FILL's adopt of the running chunk at this
            # runner's own epoch enters the node through its declared session (`adopt_enters_node`).
            _log.info("next node waits out the chunk's pause — holding envs", chunk_id=self.chunk_id)
        elif move is ApplyMove.HOLD_FOR_HUB_NODE:
            _log.info("hub node took over — holding envs until terminal", chunk_id=self.chunk_id)
        elif move is ApplyMove.RELEASE_MIGRATED:
            # A cross-graph migration already released the route — tear the attempt down;
            # the chunk is claimed afresh under the new graph rather than continued in place.
            _log.info("chunk migrated to another graph — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
        elif move is ApplyMove.RELEASE_DONE:
            self.ctx.env_release.release_chunk(self.chunk_id)
        elif move is ApplyMove.HOLD_AT_GATE:
            _log.info("chunk parked at human gate", chunk_id=self.chunk_id)  # waiting_on_human

    def drive(self, takeovers: TakeoverHolds | None = None) -> None:
        """Drive a chunk the runner holds with no active lease (:func:`held_chunk_move`).

        Every shape holds its environments until a terminal outcome. An open takeover over the
        chunk suppresses only the moves that would start a session or move the chunk on under the
        person; an ended chunk is still released and a hub node still stepped."""
        try:
            view = self.ctx.chunk_views.get(self.chunk_id)
        except ChunkNotFoundError:
            _log.warning("hub reports held chunk unknown — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
            return
        except HubClientError:
            return
        takeovers = takeovers if takeovers is not None else TakeoverHolds()
        # The local epoch is read only where a decision compares against it (`bzh:bulk-reconstitution`).
        reads_epoch = held_chunk_reads_local_epoch(view) or takeovers.covers(self.chunk_id)
        local_epoch = self.ctx.stores.lease_record.latest_epoch(self.chunk_id) if reads_epoch else 0
        move = held_chunk_move(
            view,
            runner_id=self.ctx.config.runner_id,
            local_latest_epoch=local_epoch,
            taken_over=takeovers.holds(self.chunk_id, local_epoch),
        )
        if move is HeldChunkMove.RELEASE_DONE:
            _log.info("delivery landed — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
        elif move is HeldChunkMove.RELEASE_STOPPED:
            _log.info("held chunk stopped — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
        elif move is HeldChunkMove.RELEASE_DETACHED_GATE:
            _log.info("gate-parked chunk no longer routed here — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
        elif move is HeldChunkMove.RESOLVE_GATE and view.decision is not None:
            self._resolve_gate(view.decision)
        elif move is HeldChunkMove.SPAWN_ADVANCED and view.latest_epoch is not None:
            self._spawn_advanced_node(view.latest_epoch)
        elif move is HeldChunkMove.POLL_HUB_NODE:
            # A chunk parked at a hub node — drive it one step; a no-op leaves this binding
            # held and polled again next tick.
            self._poll_hub_node(view.latest_epoch)
        # Every other shape keeps its binding and is polled again next tick.

    def _chunk_paused(self) -> bool:
        """A per-chunk pause stands at the hub. Unreadable reads as not paused — the last-known
        directive holds, and PULL parks the lease next tick if the pause is real."""
        try:
            return chunk_paused(self.ctx.chunk_views.get(self.chunk_id))
        except HubClientError:
            return False

    def _poll_hub_node(self, latest_epoch: int | None) -> None:
        """Drive a chunk parked at a hub node one step via ``POST /chunks/{id}/hub-advance`` — the
        re-drive path a hub node otherwise has no liveness poll for. A no-op or transport failure is swallowed."""
        try:
            with self.ctx.tracer.under(step_root(StepKey.attempt(self.chunk_id, (latest_epoch or 0) + 1))):
                self.ctx.hub.hub_advance(self.chunk_id)
        except HubClientError:
            return  # hub unreachable — retried next tick
        self.ctx.chunk_views.invalidate(self.chunk_id)  # a later get() this tick sees the step

    def _spawn_advanced_node(self, latest_epoch: int) -> None:
        """Spawn the held chunk's current node into its already-bound, warm environment.

        The chunk advanced while this runner retained the route, so no active lease was minted
        for it and nothing else will spawn it (#63)."""
        bindings = self.ctx.stores.environments.bindings_for_chunk(self.chunk_id)
        if not bindings:
            _log.warning("held chunk advanced with no bound env — cannot spawn", chunk_id=self.chunk_id)
            return
        try:
            with self.ctx.tracer.under(step_root(StepKey.attempt(self.chunk_id, latest_epoch))):
                envelope = self.ctx.hub.get_envelope(self.chunk_id)
        except (ChunkNotFoundError, ChunkEndedError):
            _log.warning("hub reports advanced chunk unknown — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
            return
        except HubClientError:
            return  # hub unreachable — the transition is durable at the hub; retry next tick
        _log.info("hub advanced held chunk into a fresh node — spawning", chunk_id=self.chunk_id)
        Spawner(self.ctx).enter_node(self.chunk_id, envelope, Environments(bindings).acquired, via="advance")

    def _resolve_gate(self, decision: ChunkGate) -> None:
        """Record the resolving transition for a decided gate and continue in place.

        Reuses the parked step's epoch — no new lease was minted while parked — and references
        the decision id, which is what makes a transition out of a human-judged node legal."""
        parked = self.ctx.stores.lease_record.latest_lease_for_chunk(self.chunk_id)
        submission = Completion(
            choice=decision.resolved_choice or "",
            epoch=decision.epoch,
            runner_id=self.ctx.config.runner_id,
            from_node_id=decision.node_id,
            artifacts=[],  # the decision's artifacts already landed
            decision_id=decision.decision_id,
            # Not buffered, so stamped directly at submit.
            route_token=self.ctx.stores.tokens.route_token(self.chunk_id),
            lease_id=gate_resolution_lease_id(parked, decision),
        )
        try:
            gate = StepKey.gate(self.chunk_id, decision.epoch, decision.decision_id)
            with self.ctx.tracer.under(step_root(gate)):
                response = self.ctx.hub.submit_completion(self.chunk_id, submission)
        except HubClientError:
            return  # the resolution is durable at the hub; retry next tick
        if response.outcome == ApplyOutcome.FAILURE:
            _log.warning("resolving transition rejected", chunk_id=self.chunk_id, detail=response.detail or "")
            return
        self.ctx.chunk_views.invalidate(self.chunk_id)  # a later get() this tick sees the resolution
        _log.info("gate resolved — advancing chunk", chunk_id=self.chunk_id, choice=decision.resolved_choice)
        self.apply(
            response.outcome, response.next_envelope, self.ctx.stores.environments.bindings_for_chunk(self.chunk_id)
        )
