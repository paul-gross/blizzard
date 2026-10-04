"""A chunk whose environments this runner holds, and the moves that keep it going."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.logging import get_logger
from blizzard.foundation.trace_ids import StepKey, step_root
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.spawn import Environments, Spawner
from blizzard.wire.chunk import ChunkDecisionStatusView
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.envelope import ApplyOutcome, NodeEnvelope

_log = get_logger("blizzard.runner.loop")


@dataclass(frozen=True)
class HeldChunk:
    """A chunk this runner holds environments for, driven one move at a time — by the hub's
    answer to an applied step, or by polling when no lease is active."""

    ctx: LoopContext
    chunk_id: str

    def apply(self, outcome: ApplyOutcome, next_envelope: NodeEnvelope | None, bindings: list[EnvBinding]) -> None:
        if outcome == ApplyOutcome.NEXT and next_envelope is not None:
            Spawner(self.ctx).enter_node(
                self.chunk_id, next_envelope, Environments(bindings).acquired, via="apply-response"
            )
        elif outcome == ApplyOutcome.HUB_NODE_TAKEN:
            _log.info("hub node took over — holding envs until terminal", chunk_id=self.chunk_id)
        elif outcome == ApplyOutcome.MIGRATED:
            # A cross-graph migration already released the route (#90) — tear the attempt down;
            # the chunk is claimed afresh under the new graph rather than continued in place.
            _log.info("chunk migrated to another graph — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
        elif outcome == ApplyOutcome.DONE:
            self.ctx.env_release.release_chunk(self.chunk_id)
        elif outcome == ApplyOutcome.PARKED_AT_GATE:
            _log.info("chunk parked at human gate", chunk_id=self.chunk_id)  # waiting_on_human

    def drive(self) -> None:
        """Drive a chunk the runner holds with no active lease.

        Four shapes share this poll, all holding environments until a terminal outcome: a hub node polled toward its
        terminal outcome, a resolved gate, a chunk moved to a higher epoch, and an unknown one."""
        try:
            view = self.ctx.chunk_views.get(self.chunk_id)
        except ChunkNotFoundError:
            _log.warning("hub reports held chunk unknown — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
            return
        except HubClientError:
            return
        if view.status == ChunkStatus.DONE:
            _log.info("delivery landed — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
            return
        if view.status == ChunkStatus.STOPPED:
            _log.info("held chunk stopped — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
            return
        decision = view.decision
        if decision is not None and view.route_runner_id != self.ctx.config.runner_id:
            # A gate park keeps its route ours, so a decision with the route elsewhere is a park
            # detached or reassigned out from under this runner — nothing left here to resolve.
            _log.info("gate-parked chunk no longer routed here — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
            return
        if decision is not None and decision.resolved_choice is not None and not decision.transitioned:
            self._resolve_gate(decision)
            return
        hub_epoch = view.latest_epoch
        if (
            view.status == ChunkStatus.RUNNING
            and hub_epoch is not None
            and hub_epoch > self.ctx.stores.lease_record.latest_epoch(self.chunk_id)
        ):
            # The strictly-higher epoch is load-bearing: a just-escalated chunk still derives
            # `running` at the SAME epoch until its fact flushes, and would re-spawn forever (#63).
            self._spawn_advanced_node(hub_epoch)
        elif view.status == ChunkStatus.DELIVERING:
            # A chunk parked at a hub node — drive it one step; a no-op leaves this binding
            # held and polled again next tick (#65/#66).
            self._poll_hub_node(hub_epoch)
        # Every other shape keeps its binding and is polled again next tick.

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
        except ChunkNotFoundError:
            _log.warning("hub reports advanced chunk unknown — releasing envs", chunk_id=self.chunk_id)
            self.ctx.env_release.release_chunk(self.chunk_id)
            return
        except HubClientError:
            return  # hub unreachable — the transition is durable at the hub; retry next tick
        _log.info("hub advanced held chunk into a fresh node — spawning", chunk_id=self.chunk_id)
        Spawner(self.ctx).enter_node(self.chunk_id, envelope, Environments(bindings).acquired, via="advance")

    def _resolve_gate(self, decision: ChunkDecisionStatusView) -> None:
        """Record the resolving transition for a decided gate and continue in place.

        Reuses the parked step's epoch — no new lease was minted while parked — and references
        the decision id, which is what makes a transition out of a human-judged node legal."""
        parked = self.ctx.stores.lease_record.latest_lease_for_chunk(self.chunk_id)
        submission = CompletionSubmission(
            choice=decision.resolved_choice or "",
            epoch=decision.epoch,
            runner_id=self.ctx.config.runner_id,
            from_node_id=decision.node_id,
            artifacts=[],  # the decision's artifacts already landed
            decision_id=decision.decision_id,
            # Not buffered, so stamped directly at submit.
            route_token=self.ctx.stores.tokens.route_token(self.chunk_id),
            lease_id=parked.lease_id if parked is not None and parked.epoch == decision.epoch else None,
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
