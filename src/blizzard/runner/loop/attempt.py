"""How a minted lease ends: the five terminal moves, and the closure each records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.domain.takeover import TakeoverCommand
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.spawn import Environments, Spawner
from blizzard.runner.loop.transcript_pump import PUMP_LEASE_MAX_SECONDS, TranscriptPump
from blizzard.wire.facts import EVENT_RECORDED
from blizzard.wire.sse_runner import LeaseChangeCause

_log = get_logger("blizzard.runner.loop")

# Closure reasons (lease_closures.reason).
TRANSITIONED = "transitioned"
REAPED = "reaped"
FAILED = "failed"
ESCALATED = "escalated"
PARKED = "parked"  # a runner-config gate: the node-step completed, the chunk parks on a decision
RELEASED = "released"  # the chunk was found reassigned/detached/unknown — abandon, no requeue (blizzard#9)
PREEMPTED = "preempted"  # an operator restart re-aimed the chunk (#370): envs and route kept

# ABANDON — the reassigned/detached release, in two windows. Release runs BEFORE the closure so
# the still-active lease stays the handle recovery re-derives the idempotent abandon from.
_CP_ABANDON_AFTER_KILL = crashpoint(
    "abandon.after-kill.before-release", "detached worker killed; environments not yet released"
)
_CP_ABANDON_AFTER_RELEASE = crashpoint(
    "abandon.after-release.before-closure", "environments released; the lease's closure not yet recorded"
)

# PAUSE — the operator's per-chunk pause park (issue #46): the worker dies, the claim, route,
# epoch and envs survive. A crash before the park is recovered by RESUME.
_CP_PAUSE_PARK_AFTER_KILL = crashpoint(
    "pause.after-kill.before-park", "paused worker killed; pause-park not yet durable"
)

# PREEMPT — the operator restart's teardown (#370). Between the kill and the closure the lease
# is active behind a dead pid; the hub's fence is what makes the next PULL re-derive the move.
_CP_PREEMPT_AFTER_KILL = crashpoint(
    "preempt.after-kill.before-closure", "restarted chunk's worker killed; the preempted closure not yet durable"
)

#: The classification each :meth:`Attempt.fail` branch surfaces (issue #125). The
#: locally-paused defer branch surfaces nothing — a deferral is not an outcome.
_ATTEMPT_FAILED = ("warning", "attempt-failed")
_WORKER_LOST = ("critical", "worker-lost")
_ATTEMPT_ABANDONED = ("info", "attempt-abandoned")


@dataclass(frozen=True)
class Attempt:
    """One minted lease, and the moves that end it — fail (which requeues or escalates),
    abandon, park on an operator pause, and preempt on an operator restart.

    Which move a caller takes is decided by the node's retry budget, by whether the hub still
    routes this chunk here, and by whether its fence rose; each records its own closure."""

    ctx: LoopContext
    lease: LeaseRecord

    def fail(self, *, reason: LeaseChangeCause, via: str) -> None:
        """Close a failed attempt, then requeue at the node or escalate per the budget.

        An escalation is a one-way door this same tick's flush cannot retract, so the
        exhausted-retries branch re-asks the ownership question first (blizzard#38) and defers
        entirely while locally paused (issue #45). The requeue branch needs no such gate."""
        lease = self.lease
        now = self.ctx.clock.now()
        if lease.pid is not None:
            self.ctx.process.kill(lease.pid)  # best-effort hygiene; the epoch fence is the guarantee
        # Best-effort: a worker that never crashed to stderr wrote no tail, the ordinary case.
        tail = self.ctx.worker_files.stderr_tail(lease)

        # attempt_count includes this lease, and a first attempt is not a retry.
        retried = self.ctx.store.attempt_count(lease.chunk_id, lease.node_id) - 1
        if retried < lease.retries_max:
            # Retry: enqueued ATOMICALLY with the closure it describes (issue #125).
            self.close(
                reason,
                now,
                self._event(_ATTEMPT_FAILED, f"attempt failed, retrying — {reason} (via {via})", reason, via, tail),
            )
            self.requeue()
            return
        if self.detached():
            # Emitted HERE rather than in `abandon`, which the ordinary detach sweep also
            # reaches and which must stay silent.
            OutboundFacts(self.ctx).event(
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                at=now,
                payload=self._event(
                    _ATTEMPT_ABANDONED, f"attempt abandoned — chunk reassigned ({reason}, via {via})", reason, via, tail
                ),
            )
            self.abandon(killed=True, via=via)
            return
        if self.ctx.store.local_paused(self.ctx.config.runner_id):
            # Deliberate deferral, not a surfaced failure — emit nothing (issue #125).
            _log.info(
                "escalation deferred — locally paused",
                runner_id=self.ctx.config.runner_id,
                via=via,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
            )
            return
        self.close(
            ESCALATED,
            now,
            self._event(_WORKER_LOST, f"worker lost — retries exhausted ({reason}, via {via})", reason, via, tail),
        )
        self.escalate()

    def requeue(self) -> None:
        """Re-attempt the node in the same environments — new session, new lease, fresh epoch.

        The prior attempt's lease is already closed before this runs, so a 404 here leaves no
        active lease behind for any later sweep to clean up — the binding would be held
        forever. It is therefore released here rather than retried (blizzard#9)."""
        lease = self.lease
        bindings = self.ctx.store.bindings_for_chunk(lease.chunk_id)
        if not bindings:
            _log.warning("requeue with no bound env — cannot re-spawn", chunk_id=lease.chunk_id)
            return
        try:
            envelope = self.ctx.hub.get_envelope(lease.chunk_id)  # idempotent re-read
        except ChunkNotFoundError:
            _log.warning("hub reports chunk unknown at requeue — releasing envs", chunk_id=lease.chunk_id)
            self.ctx.env_release.release_chunk(lease.chunk_id)
            return
        except HubClientError:
            return  # the closed attempt is durable; FILL/ADVANCE re-drives next tick
        _log.info("requeuing at node", chunk_id=lease.chunk_id, node=lease.node_name)
        Spawner(self.ctx).spawn(lease.chunk_id, envelope, Environments(bindings).acquired, via="requeue")

    def escalate(self, *, reason: str = "retries exhausted") -> None:
        """Park the chunk needs-human at the hub, envs held for takeover."""
        lease = self.lease
        bindings = self.ctx.store.bindings_for_chunk(lease.chunk_id)
        takeover = ""
        wrapped = ""
        if lease.session_id is not None and bindings:
            # Composed from the lease's own stamps (issue #144), so a takeover lands in exactly
            # the configuration the parked session ran with, never a fresh resolution.
            takeover = self.ctx.harness.resume_command(
                bindings[0].workdir, lease.session_id, model=lease.resolved_model, effort=lease.resolved_effort
            )
            # Wrapped-vs-raw rules: `blizzard-context:/domain/humans/escalation.md` §The commands an escalation carries.
            if self.ctx.config.runner_dir:
                wrapped = TakeoverCommand(lease.chunk_id, self.ctx.config.runner_dir).wrapped
        else:
            # No session ever recorded, or its bindings already released — both compose nothing.
            # The two fields say which, per `blizzard-context:/domain/humans/escalation.md` §What each origin carries.
            _log.warning(
                "escalating with no takeover command",
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                has_session=lease.session_id is not None,
                bound_envs=len(bindings),
            )
        OutboundFacts(self.ctx).escalation(lease, takeover=takeover, wrapped_takeover=wrapped, at=self.ctx.clock.now())
        _log.info(
            "escalated to needs-human", reason=reason, chunk_id=lease.chunk_id, takeover=takeover, wrapped=wrapped
        )

    def abandon(self, *, killed: bool = False, via: str) -> None:
        """Release a chunk the hub reassigned, detached, or no longer knows about (blizzard#9) —
        reached from restart-resume or a live tick.

        No epoch bump and no requeue — the work is not this runner's any more. The lease closes
        ``released``, and any open ask park is retired alongside (blizzard#202)."""
        lease = self.lease
        now = self.ctx.clock.now()
        if lease.pid is not None and not killed:
            self.ctx.process.kill(lease.pid)
        _CP_ABANDON_AFTER_KILL.reached()  # recovery is the next tick's re-scan
        self.ctx.env_release.release_chunk(lease.chunk_id)
        _CP_ABANDON_AFTER_RELEASE.reached()  # re-run releases nothing more, then records the closure
        park = self.ctx.store.open_park(lease.lease_id)
        if park is not None:
            self.ctx.store.record_park_resume(lease_id=lease.lease_id, question_id=park.question_id, resumed_at=now)
        self.close(RELEASED, now)
        self.ctx.store.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        _log.info(
            "abandoned reassigned/detached/unknown chunk", chunk_id=lease.chunk_id, lease_id=lease.lease_id, via=via
        )

    def park_paused(self, *, via: str) -> None:
        """Kill a paused chunk's worker and park its lease — the claim is **kept** (issue #46).

        The deliberate inverse of :meth:`abandon`: no environment released, no closure, no epoch
        bump, no lease minted — **no retry is consumed**, and the route, epoch and session all
        survive. Not gated by the local brake: a kill is not a spawn."""
        lease = self.lease
        now = self.ctx.clock.now()
        if lease.pid is not None:
            self.ctx.process.kill(lease.pid)
        _CP_PAUSE_PARK_AFTER_KILL.reached()  # worker dead; the park is not yet durable
        self.ctx.store.record_pause_park(lease_id=lease.lease_id, chunk_id=lease.chunk_id, parked_at=now)
        self.ctx.store.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        if self.ctx.events is not None:
            # Same "dormant" cause `park_on_ask` publishes (dormant.py) — this write flips the
            # same LeaseActivity.state to "parked", just via the operator-pause path.
            self.ctx.events.publish_lease_changed(
                lease.lease_id,
                lease.chunk_id,
                cause="dormant",
            )
        _log.info(
            "parked chunk on an operator pause — claim retained",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            via=via,
        )

    def preempt(self, *, via: str) -> None:
        """Tear down an attempt an operator's restart superseded, and re-enter the node (#370).

        Inverse of :meth:`abandon` in what survives — route, tenure and envs stay this runner's
        — and no retry is consumed. Deferred entirely while locally paused, as :meth:`fail`'s
        escalation branch defers (#45): the re-entry is a spawn that brake suppresses."""
        lease = self.lease
        if self.ctx.store.local_paused(self.ctx.config.runner_id):
            _log.info(
                "preempt deferred — locally paused",
                runner_id=self.ctx.config.runner_id,
                via=via,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
            )
            return
        now = self.ctx.clock.now()
        if lease.pid is not None:
            self.ctx.process.kill(lease.pid)  # best-effort hygiene; the epoch fence is the guarantee
        _CP_PREEMPT_AFTER_KILL.reached()  # recovery is the next tick's re-scan, off the still-higher fence
        park = self.ctx.store.open_park(lease.lease_id)
        if park is not None:
            self.ctx.store.record_park_resume(lease_id=lease.lease_id, question_id=park.question_id, resumed_at=now)
        self.close(PREEMPTED, now)
        self.ctx.store.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        _log.info("preempted by an operator restart", chunk_id=lease.chunk_id, lease_id=lease.lease_id, via=via)
        self.reenter()

    def reenter(self) -> None:
        """Spawn the chunk's re-aimed node into the environments this runner still holds (#370).

        Whether that spawn resumes anything is the envelope's to say, not this call's. The
        preempted attempt is already closed, so a hub failure here leaves the chunk held with no
        lease — the shape ADVANCE's held-chunk poll re-drives next tick."""
        lease = self.lease
        bindings = self.ctx.store.bindings_for_chunk(lease.chunk_id)
        if not bindings:
            _log.warning("restart with no bound env — cannot re-enter", chunk_id=lease.chunk_id)
            return
        try:
            envelope = self.ctx.hub.get_envelope(lease.chunk_id)
        except ChunkNotFoundError:
            _log.warning("hub reports chunk unknown at restart — releasing envs", chunk_id=lease.chunk_id)
            self.ctx.env_release.release_chunk(lease.chunk_id)
            return
        except HubClientError:
            # The closure is durable and the worker already dead, so say so: ADVANCE's held-chunk
            # poll re-drives it next tick, but nothing else would surface the gap meanwhile.
            _log.warning("hub unreachable at restart re-entry — chunk held with no lease", chunk_id=lease.chunk_id)
            return
        _log.info("re-entering node on an operator restart", chunk_id=lease.chunk_id, node=envelope.node.node_name)
        Spawner(self.ctx).enter_node(lease.chunk_id, envelope, Environments(bindings).acquired, via="restart")

    def detached(self) -> bool:
        """True iff the hub no longer routes this chunk here, or it is gone outright (blizzard#9).

        Unreachable hub → ``False``: a transport failure is never read as a detach. A 404 is the
        one exception — terminal, not something to wait out."""
        try:
            detail = self.ctx.hub.get_chunk(self.lease.chunk_id)
        except ChunkNotFoundError:
            return True  # the chunk no longer exists at the hub — terminal, not retryable
        except HubClientError:
            return False  # hub unreachable — last-known directive holds; keep working
        return detail.route is None or detail.route.runner_id != self.ctx.config.runner_id

    def close(self, reason: LeaseChangeCause, at: datetime, event: dict[str, object] | None = None) -> None:
        """Close this lease. An ``event`` lands in the outbound buffer in the same transaction
        as the closure it describes (issue #125), so the two are never seen apart. Every
        closure path funnels through here — the one place to pump this lease's own open
        transcript segment(s) before ``record_closure`` finalizes them (issue #246)."""
        self._pump_lease_before_close()
        event_seq = self.ctx.store.record_closure(
            lease_id=self.lease.lease_id,
            chunk_id=self.lease.chunk_id,
            node_id=self.lease.node_id,
            reason=reason,
            closed_at=at,
            event_kind=EVENT_RECORDED if event else None,
            event_payload=json.dumps(event) if event else None,
        )
        if self.ctx.events is not None:
            lease_id = self.lease.lease_id
            # `reason` IS the LeaseChangeCause vocabulary (D4) — enforced by `close`'s and
            # `fail`'s own parameter type now, not by a comment's claim about callers.
            self.ctx.events.publish_lease_changed(
                lease_id,
                self.lease.chunk_id,
                cause=reason,
            )
            if reason == ESCALATED:
                # `open_escalations()`'s derivation (D4) — a closed-`escalated` lease not yet
                # superseded — begins reading open at exactly this instant.
                self.ctx.events.publish_escalation_changed(self.lease.chunk_id, cause="opened", lease_id=lease_id)
            if event_seq is not None:
                # The optional operational event `record_closure` buffered alongside the
                # closure — its own fact-log row, distinct from the lease-changed frame above.
                self.ctx.events.publish_fact_changed(
                    seq=event_seq,
                    kind=EVENT_RECORDED,
                    chunk_id=self.lease.chunk_id,
                    lease_id=lease_id,
                )

    def _pump_lease_before_close(self) -> None:
        """D3's promise applies here too, weaker: exceptions never fail the closure,
        but delay is bounded, not eliminated — ``deadline`` is checked only BETWEEN
        ``_pump_one`` calls, so one in-flight read can run past ``PUMP_LEASE_MAX_SECONDS``,
        and it is minted fresh per call, so N closing leases pay it up to N times."""
        deadline = self.ctx.clock.now() + timedelta(seconds=PUMP_LEASE_MAX_SECONDS)
        try:
            TranscriptPump(self.ctx).pump_lease(self.lease.lease_id, deadline=deadline)
        except Exception:
            _log.exception(
                "transcript pump failed ahead of lease closure — closing anyway",
                lease_id=self.lease.lease_id,
                chunk_id=self.lease.chunk_id,
            )

    def _event(
        self, classification: tuple[str, str], message: str, reason: str, via: str, stderr_tail: str
    ) -> dict[str, object]:
        """The ``event.recorded`` payload one :meth:`fail` branch surfaces (issue #125), whose
        ``detail`` carries the ``(reason, via)`` that classified it and any captured stderr tail."""
        severity, kind = classification
        detail: dict[str, object] = {"via": via, "reason": reason, "node": self.lease.node_name}
        if stderr_tail:
            detail["stderr_tail"] = stderr_tail
        return {
            "severity": severity,
            "kind": kind,
            "chunk_id": self.lease.chunk_id,
            "lease_id": self.lease.lease_id,
            "node_name": self.lease.node_name,
            "message": message,
            "detail": detail,
        }
