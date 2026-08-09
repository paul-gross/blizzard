"""How a minted lease ends: the four terminal moves, and the closure each records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.takeover import TakeoverCommand
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.spawn import Environments, Spawner
from blizzard.runner.loop.transcript_pump import PUMP_LEASE_MAX_SECONDS, TranscriptPump
from blizzard.runner.store.repository import LeaseRecord
from blizzard.wire.facts import EVENT_RECORDED

_log = get_logger("blizzard.runner.loop")

# Closure reasons (lease_closures.reason).
TRANSITIONED = "transitioned"
REAPED = "reaped"
FAILED = "failed"
ESCALATED = "escalated"
PARKED = "parked"  # a runner-config gate: the node-step completed, the chunk parks on a decision
RELEASED = "released"  # the chunk was found reassigned/detached/unknown — abandon, no requeue (blizzard#9)

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

#: The classification each :meth:`Attempt.fail` branch surfaces (issue #125). The
#: locally-paused defer branch surfaces nothing — a deferral is not an outcome.
_ATTEMPT_FAILED = ("warning", "attempt-failed")
_WORKER_LOST = ("critical", "worker-lost")
_ATTEMPT_ABANDONED = ("info", "attempt-abandoned")


@dataclass(frozen=True)
class Attempt:
    """One minted lease, and the moves that end it — fail (which requeues or escalates),
    abandon, and park on an operator pause.

    Which move a caller takes is decided by the node's retry budget and by whether the hub
    still routes this chunk here; each records its own closure and surfaces its own event."""

    ctx: LoopContext
    lease: LeaseRecord

    def fail(self, *, reason: str, via: str) -> None:
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
            # Wrapped-vs-raw rules: `blizzard-context:/domain/humans.md` §Escalation.
            if self.ctx.config.runner_dir:
                wrapped = TakeoverCommand(lease.chunk_id, self.ctx.config.runner_dir).wrapped
        else:
            # No session ever recorded, or its bindings already released — both compose nothing.
            # The two fields say which, per `blizzard-context:/domain/humans.md` §Escalation.
            _log.warning(
                "escalating with no takeover command",
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                has_session=lease.session_id is not None,
                bound_envs=len(bindings),
            )
        OutboundFacts(self.ctx).escalation(lease, takeover=takeover, wrapped_takeover=wrapped, at=self.ctx.clock.now())
        _log.info(f"escalated to needs-human — {reason}", chunk_id=lease.chunk_id, takeover=takeover, wrapped=wrapped)

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
        _log.info(
            "parked chunk on an operator pause — claim retained",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            via=via,
        )

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

    def close(self, reason: str, at: datetime, event: dict[str, object] | None = None) -> None:
        """Close this lease. An ``event`` lands in the outbound buffer in the same transaction
        as the closure it describes (issue #125), so the two are never seen apart. Every
        closure path funnels through here — the one place to pump this lease's own open
        transcript segment(s) before ``record_closure`` finalizes them (issue #246)."""
        self._pump_lease_before_close()
        self.ctx.store.record_closure(
            lease_id=self.lease.lease_id,
            chunk_id=self.lease.chunk_id,
            node_id=self.lease.node_id,
            reason=reason,
            closed_at=at,
            event_kind=EVENT_RECORDED if event else None,
            event_payload=json.dumps(event) if event else None,
        )

    def _pump_lease_before_close(self) -> None:
        """D3's promise applies here too (review F4): bounded like the tick's ordinary
        drain, and exception-isolated, so neither a slow transcript read nor a raised
        exception can delay or fail the closure itself."""
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
