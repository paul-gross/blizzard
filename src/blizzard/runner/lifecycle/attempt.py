"""How a minted lease ends: the five terminal moves, and the closure each records."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.foundation.logging import get_logger
from blizzard.foundation.runner_event_types import LeaseChangeCause
from blizzard.foundation.trace_ids import StepKey, step_root
from blizzard.runner.harness.adapter import IHarnessWorkerLifecycle
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.hub.client import ChunkEndedError, ChunkNotFoundError, HubClientError
from blizzard.runner.hub.outbound import OutboundFacts
from blizzard.runner.leases.closure import NO_ACCEPTABLE_HARNESS_MINT
from blizzard.runner.leases.model import Lease
from blizzard.runner.lifecycle.model import (
    FailureMove,
    OwnerUnresolvableMove,
    failure_move,
    owner_unresolvable_closure,
    owner_unresolvable_move,
    retry_owner_admitted,
    routed_away,
)
from blizzard.runner.lifecycle.registration import registered_runner_id
from blizzard.runner.lifecycle.session import SkippedHarness
from blizzard.runner.lifecycle.spawn import Environments, SpawnContext, Spawner
from blizzard.runner.lifecycle.takeover import EscalationCommands
from blizzard.runner.process.owned_process import interrupt_owned_process, kill_owned_process
from blizzard.runner.transcripts.transcript_pump import PUMP_LEASE_MAX_SECONDS, TranscriptPump

_log = get_logger("blizzard.runner.loop")

# ABANDON — the reassigned/detached release, in two windows. Release runs BEFORE the closure so
# the still-active lease stays the handle recovery re-derives the idempotent abandon from.
_CP_ABANDON_AFTER_KILL = crashpoint(
    "abandon.after-kill.before-release", "detached worker killed; environments not yet released"
)
_CP_ABANDON_AFTER_RELEASE = crashpoint(
    "abandon.after-release.before-closure", "environments released; the lease's closure not yet recorded"
)

# PAUSE — the per-chunk pause park: interrupted, claim kept; RESUME's re-run re-signals only a live group.
_CP_PAUSE_PARK_AFTER_INTERRUPT = crashpoint(
    "pause.after-interrupt.before-park", "paused worker interrupted; pause-park not yet durable"
)

# PREEMPT — the operator restart's teardown (#370). Between the kill and the closure the lease
# is active behind a dead pid; the hub's fence is what makes the next PULL re-derive the move.
_CP_PREEMPT_AFTER_KILL = crashpoint(
    "preempt.after-kill.before-closure", "restarted chunk's worker killed; the preempted closure not yet durable"
)

#: The classification each :meth:`Attempt.fail` branch surfaces. The
#: locally-paused defer branch surfaces nothing — a deferral is not an outcome.
_ATTEMPT_FAILED: EventLogKind = "attempt-failed"
_WORKER_LOST: EventLogKind = "worker-lost"
_ATTEMPT_ABANDONED: EventLogKind = "attempt-abandoned"

#: Surfaced when an existing session's recorded harness owner cannot be dispatched to.
_OWNER_UNRESOLVABLE: EventLogKind = "owner-unresolvable"

#: Surfaced when a fresh mint's every acceptable harness is unknown, unavailable, or resolves no authored tier.
_NO_ACCEPTABLE_HARNESS: EventLogKind = "no-acceptable-harness"


class AttemptContext(SpawnContext, Protocol): ...


@dataclass(frozen=True)
class Attempt:
    """One minted lease, and the moves that end it — fail (which requeues or escalates),
    abandon, park on an operator pause, and preempt on an operator restart. Each records its
    own closure."""

    ctx: AttemptContext
    lease: Lease

    def _kill_process(self) -> None:
        """Best-effort teardown of this lease's own worker process — the shared,
        liveness-checked, pgid-preferring kill (:func:`kill_owned_process`) every owned-process
        teardown in the runner loop reaches through, rather than each reimplementing its own
        liveness check."""
        lease = self.lease
        kill_owned_process(
            self.ctx.process, pid=lease.pid, process_start_time=lease.process_start_time, pgid=lease.pgid
        )

    def fail(self, *, reason: LeaseChangeCause, via: str) -> None:
        """Close a failed attempt, then abandon, retry, defer, or escalate per :func:`failure_move`."""
        lease = self.lease
        now = self.ctx.clock.now()
        self._kill_process()  # best-effort hygiene; the epoch fence is the guarantee
        self._kill_in_flight_elicitation()
        # Best-effort: a worker that never crashed to stderr wrote no tail, the ordinary case.
        tail = self.ctx.worker_files.stderr_tail(lease)

        # attempt_count includes this lease, and a first attempt is not a retry.
        retried = self.ctx.stores.lease_record.attempt_count(lease.chunk_id, lease.node_id) - 1
        owner_block = self._owner_block()
        move = failure_move(
            retried=retried,
            retries_max=lease.retries_max,
            owner_blocked=owner_block is not None,
            detached=self.detached(),
            braked=self._braked(),
        )
        if move is FailureMove.ABANDON:
            # Emitted HERE rather than in `abandon`, which the ordinary detach sweep also
            # reaches and which must stay silent.
            OutboundFacts(self.ctx).event(
                kind=_ATTEMPT_ABANDONED,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                node_name=lease.node_name,
                message=f"attempt abandoned — chunk reassigned ({reason}, via {via})",
                detail=self._detail(reason, via, tail),
                at=now,
            )
            self.abandon(killed=True, via=via)
            return
        if move is FailureMove.RETRY:
            # Retry: enqueued ATOMICALLY with the closure it describes.
            self.close(
                reason,
                now,
                self._event(_ATTEMPT_FAILED, f"attempt failed, retrying — {reason} (via {via})", reason, via, tail),
            )
            self.requeue()
            return
        if move is FailureMove.DEFER:
            # Deliberate deferral, not a surfaced failure — emit nothing.
            _log.info(
                "escalation deferred — locally paused",
                runner_name=self.ctx.config.runner_name,
                via=via,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
            )
            return
        if move is FailureMove.ESCALATE_OWNER_UNRESOLVABLE and owner_block is not None:
            # The precedence already ran above for this lease, so this calls the actual close
            # directly rather than re-checking it via the public precedence gate.
            session, exc = owner_block
            self._escalate_owner_unresolvable(session=session, exc=exc, via=via)
            return
        self.close(
            LeaseClosureReason.ESCALATED,
            now,
            self._event(_WORKER_LOST, f"worker lost — retries exhausted ({reason}, via {via})", reason, via, tail),
            escalation_cause=EscalationCause.RETRIES_EXHAUSTED,
        )
        self.escalate(
            cause=EscalationCause.RETRIES_EXHAUSTED,
            detail=f"retries {retried} of {lease.retries_max} used; last failure: {reason} (via {via})",
        )

    def requeue(self) -> None:
        """Re-attempt the node in the same environments — new session, new lease, fresh epoch.

        The prior attempt's lease is already closed before this runs, so a 404 here leaves no
        active lease behind for any later sweep to clean up — the binding would be held
        forever. It is therefore released here rather than retried."""
        lease = self.lease
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        if not bindings:
            _log.warning("requeue with no bound env — cannot re-spawn", chunk_id=lease.chunk_id)
            return
        try:
            with self.ctx.tracer.under(step_root(StepKey.attempt(lease.chunk_id, lease.epoch))):
                envelope = self.ctx.hub.get_envelope(lease.chunk_id)  # idempotent re-read
        except (ChunkNotFoundError, ChunkEndedError):
            _log.warning("hub reports chunk unknown at requeue — releasing envs", chunk_id=lease.chunk_id)
            self.ctx.env_release.release_chunk(lease.chunk_id)
            return
        except HubClientError:
            return  # the closed attempt is durable; FILL/ADVANCE re-drives next tick
        acceptable = envelope.node.session_harnesses
        if not retry_owner_admitted(lease.harness_id, acceptable):
            # The failed attempt's owner has fallen out of a since-edited acceptable set — a
            # membership check, not resolvability (`_owner_block` already ran that half above).
            assert lease.harness_id is not None  # every requeue-reachable lease was minted under a recorded owner
            Spawner(self.ctx).escalate_no_acceptable_harness(
                lease.chunk_id,
                envelope,
                attempted=acceptable,
                skipped=(SkippedHarness(lease.harness_id, "not-a-member"),),
                via="requeue",
            )
            return
        _log.info("requeuing at node", chunk_id=lease.chunk_id, node=lease.node_name)
        # A retry mints a new session, but stays under the failed lease's own mint owner —
        # carried even when that mint never reached spawn-return.
        Spawner(self.ctx).spawn(
            lease.chunk_id,
            envelope,
            Environments(bindings).acquired,
            via="requeue",
            harness_id=lease.harness_id,
        )

    def escalate(self, *, cause: EscalationCause, detail: str) -> None:
        """Park the chunk needs-human at the hub, envs held for takeover, recording why.

        Reached only after the closure is already durable (:meth:`fail` closes first), so an
        unresolvable owner cannot un-escalate it — it only costs the takeover command, which
        the "no takeover command" branch below already covers."""
        lease = self.lease
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        session = lease.session
        harness = self._resolve_harness(session, via="escalate") if session is not None else None
        commands = EscalationCommands.compose(
            lease.chunk_id,
            session=session,
            bindings=bindings,
            harness=harness,
            model=lease.resolved_model,
            effort=lease.resolved_effort,
            workspace_root=self.ctx.config.workspace_root,
            runner_dir=self.ctx.config.runner_dir,
        )
        takeover = commands.resume
        wrapped = commands.wrapped or ""
        if not takeover:
            # No session, released bindings, or an unresolvable owner all compose nothing; the
            # logged fields say which (`blizzard-context:/domain/humans/escalation.md` §What each origin carries).
            _log.warning(
                "escalating with no takeover command",
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                has_session=lease.session_id is not None,
                bound_envs=len(bindings),
                harness_unresolved=session is not None and harness is None,
            )
        OutboundFacts(self.ctx).escalation(
            lease, takeover=takeover, wrapped_takeover=wrapped, cause=cause, detail=detail, at=self.ctx.clock.now()
        )
        _log.info(
            "escalated to needs-human",
            cause=str(cause),
            detail=detail,
            chunk_id=lease.chunk_id,
            takeover=takeover,
            wrapped=wrapped,
        )

    def escalate_owner_unresolvable(
        self, *, session: SessionReference, exc: UnknownHarnessError | UnavailableHarnessError, via: str
    ) -> None:
        """Escalate this still-OPEN lease in place because its recorded owner cannot be
        dispatched to right now — no other runner can resume this exact session. Takes the
        same detached/paused precedence :meth:`fail` takes ahead of its own exhausted-retries
        escalation."""
        lease = self.lease
        now = self.ctx.clock.now()
        move = owner_unresolvable_move(detached=self.detached(), braked=self._braked())
        if move is OwnerUnresolvableMove.ABANDON:
            OutboundFacts(self.ctx).event(
                kind=_ATTEMPT_ABANDONED,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                node_name=lease.node_name,
                message=f"attempt abandoned — chunk reassigned (owner unresolvable, via {via})",
                detail={"via": via, "harness_id": session.harness_id},
                at=now,
            )
            self.abandon(via=via)
            return
        if move is OwnerUnresolvableMove.DEFER:
            # Deliberate deferral, not a surfaced escalation (mirrors `fail`'s own defer) — the
            # lease stays open, untouched, for a later pass once the pause lifts.
            _log.info(
                "owner-unresolvable escalation deferred — locally paused",
                runner_name=self.ctx.config.runner_name,
                via=via,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
            )
            return
        self._escalate_owner_unresolvable(session=session, exc=exc, via=via)

    def _escalate_owner_unresolvable(
        self, *, session: SessionReference, exc: UnknownHarnessError | UnavailableHarnessError, via: str
    ) -> None:
        """The actual close: :meth:`escalate_owner_unresolvable`'s own body, reached once its
        detached/paused precedence has cleared. Unlike :meth:`escalate` (reached only after
        ``fail`` has already closed the lease), this closes it itself, so a crash right after
        leaves nothing to redo — the next pass reads a closed lease."""
        lease = self.lease
        now = self.ctx.clock.now()
        self._kill_process()  # best-effort hygiene; nothing is live behind it
        self._kill_in_flight_elicitation()
        closure = owner_unresolvable_closure(lease, unavailable=isinstance(exc, UnavailableHarnessError))
        status = closure.owner_status
        message = f"escalated — recorded harness owner {status} ({session.harness_id!r}, via {via})"
        event = {
            "severity": EVENT_LOG_SEVERITY[_OWNER_UNRESOLVABLE],
            "kind": _OWNER_UNRESOLVABLE,
            "chunk_id": lease.chunk_id,
            "lease_id": lease.lease_id,
            "node_name": lease.node_name,
            "message": message,
            "detail": {"via": via, "harness_id": session.harness_id, "owner_status": status},
        }
        self.close(
            LeaseClosureReason.ESCALATED,
            now,
            event,
            closure_reason=closure.closure_reason,
            escalation_cause=EscalationCause.OWNER_UNRESOLVABLE,
        )
        # Resolved inline, same as `abandon`/`park_paused`/`preempt` — escalated is a third
        # resolution `record_resume_clear` closes here, not a fourth pending state.
        self.ctx.stores.resume_intent.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        self.escalate(
            cause=EscalationCause.OWNER_UNRESOLVABLE,
            detail=f"recorded harness owner {session.harness_id!r} is {status} (via {via})",
        )

    def escalate_no_acceptable_harness(
        self, *, attempted: Sequence[str], skipped: Sequence[SkippedHarness], via: str
    ) -> None:
        """Escalate this owner-less, never-spawned lease because no acceptable harness could
        serve the mint — reached alike from a fresh mint's exhausted selection and from
        :meth:`requeue`'s membership guard. Never an open lease, so no pid to kill."""
        lease = self.lease
        now = self.ctx.clock.now()
        message = f"escalated — no acceptable harness could serve this mint (via {via})"
        event = {
            "severity": EVENT_LOG_SEVERITY[_NO_ACCEPTABLE_HARNESS],
            "kind": _NO_ACCEPTABLE_HARNESS,
            "chunk_id": lease.chunk_id,
            "lease_id": lease.lease_id,
            "node_name": lease.node_name,
            "message": message,
            "detail": {
                "via": via,
                "attempted": list(attempted),
                "skipped": [{"harness_id": s.harness_id, "reason": s.reason} for s in skipped],
            },
        }
        self.close(
            LeaseClosureReason.ESCALATED,
            now,
            event,
            closure_reason=NO_ACCEPTABLE_HARNESS_MINT,
            escalation_cause=EscalationCause.NO_ACCEPTABLE_HARNESS,
        )
        # Same resolve-inline shape as `_escalate_owner_unresolvable`.
        self.ctx.stores.resume_intent.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        tried = ", ".join(f"{s.harness_id}: {s.reason}" for s in skipped) or "none skipped"
        self.escalate(
            cause=EscalationCause.NO_ACCEPTABLE_HARNESS,
            detail=f"attempted {', '.join(attempted) or 'none'} — {tried} (via {via})",
        )

    def abandon(self, *, killed: bool = False, via: str) -> None:
        """Release a chunk the hub reassigned, detached, or no longer knows about —
        reached from restart-resume or a live tick.

        No epoch bump and no requeue — the work is not this runner's any more. The lease closes
        ``released``, and any open ask park is retired alongside."""
        lease = self.lease
        now = self.ctx.clock.now()
        if not killed:
            self._kill_process()
        self._kill_in_flight_elicitation()
        _CP_ABANDON_AFTER_KILL.reached()  # recovery is the next tick's re-scan
        self.ctx.env_release.release_chunk(lease.chunk_id)
        _CP_ABANDON_AFTER_RELEASE.reached()  # re-run releases nothing more, then records the closure
        park = self.ctx.stores.asks.open_park(lease.lease_id)
        if park is not None:
            self.ctx.stores.asks.record_park_resume(
                lease_id=lease.lease_id, question_id=park.question_id, resumed_at=now
            )
        self.close(LeaseClosureReason.RELEASED, now)
        self.ctx.stores.resume_intent.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        _log.info(
            "abandoned reassigned/detached/unknown chunk", chunk_id=lease.chunk_id, lease_id=lease.lease_id, via=via
        )

    def park_paused(self, *, via: str) -> None:
        """Interrupt a paused chunk's worker and park its lease — the claim is **kept**: the
        inverse of :meth:`abandon`, nothing released, closed, bumped, or minted, no retry consumed. Multi-tick:
        this only SIGINTs the worker's and any in-flight elicitation's groups, then records the
        park naming that elicitation; ``DormantSession.on_unpause`` finishes the teardown on later ticks — a
        survivor is SIGKILLed only past ``SHUTDOWN_DRAIN_DEADLINE`` from ``parked_at`` — so the envelope survives."""
        lease = self.lease
        now = self.ctx.clock.now()
        interrupt_owned_process(
            self.ctx.process, pid=lease.pid, process_start_time=lease.process_start_time, pgid=lease.pgid
        )
        elicitation = self.ctx.stores.elicitations.in_flight_elicitation(lease.lease_id, lease.epoch)
        interrupted_elicitation_id: int | None = None
        if elicitation is not None:
            interrupt_owned_process(
                self.ctx.process,
                pid=elicitation.pid,
                process_start_time=elicitation.process_start_time,
                pgid=elicitation.pgid,
            )
            interrupted_elicitation_id = elicitation.id
        _CP_PAUSE_PARK_AFTER_INTERRUPT.reached()  # worker signalled; the park is not yet durable
        self.ctx.stores.pause.record_pause_park(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            parked_at=now,
            interrupted_elicitation_id=interrupted_elicitation_id,
        )
        self.ctx.stores.resume_intent.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        if self.ctx.events is not None:
            # Same "dormant" cause `park_on_ask` publishes (src/blizzard/runner/lifecycle/dormant.py) —
            # this write flips the same LeaseActivity.state to "parked", just via the operator-pause path.
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

    def park_usage_limited(self) -> None:
        """Park a lease whose exited generation was classified usage-limited —
        the same durable park :meth:`park_paused` records, minus the kill: the worker has
        already exited by the time a usage limit is classified, so there is nothing live to
        tear down, and any in-flight elicitation record is the caller's own to clear (its
        own ``relaunch_count`` lives on that record, not here). The claim, route, epoch and
        session all survive; :class:`~blizzard.runner.lifecycle.dormant.DormantSession` resumes it
        once the local brake the caller engaged ahead of this call lifts."""
        lease = self.lease
        now = self.ctx.clock.now()
        self.ctx.stores.pause.record_pause_park(lease_id=lease.lease_id, chunk_id=lease.chunk_id, parked_at=now)
        self.ctx.stores.resume_intent.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        if self.ctx.events is not None:
            # Same "dormant" cause `park_paused` publishes — this write flips the same
            # LeaseActivity.state to "parked", just via the usage-limit path.
            self.ctx.events.publish_lease_changed(lease.lease_id, lease.chunk_id, cause="dormant")
        _log.info(
            "parked chunk on a usage-limit pause — claim retained",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
        )

    def preempt(self, *, via: str) -> None:
        """Tear down an attempt an operator's restart superseded, and re-enter the node.

        Inverse of :meth:`abandon` in what survives — route, tenure and envs stay this runner's — and
        no retry is consumed. The fenced-out worker is killed and closed even under the local brake;
        only the re-entry spawn waits for it to lift."""
        lease = self.lease
        now = self.ctx.clock.now()
        self._kill_process()  # best-effort hygiene; the epoch fence is the guarantee
        self._kill_in_flight_elicitation()
        _CP_PREEMPT_AFTER_KILL.reached()  # recovery is the next tick's re-scan, off the still-higher fence
        park = self.ctx.stores.asks.open_park(lease.lease_id)
        if park is not None:
            self.ctx.stores.asks.record_park_resume(
                lease_id=lease.lease_id, question_id=park.question_id, resumed_at=now
            )
        self.close(LeaseClosureReason.PREEMPTED, now)
        self.ctx.stores.resume_intent.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        _log.info("preempted by an operator restart", chunk_id=lease.chunk_id, lease_id=lease.lease_id, via=via)
        self.reenter()

    def reenter(self) -> None:
        """Spawn the chunk's re-aimed node into the environments this runner still holds (#370).

        Whether that spawn resumes anything is the envelope's to say, not this call's. The
        preempted attempt is already closed, so a hub failure here leaves the chunk held with no
        lease — the shape ADVANCE's held-chunk poll re-drives next tick."""
        lease = self.lease
        bindings = self.ctx.stores.environments.bindings_for_chunk(lease.chunk_id)
        if not bindings:
            _log.warning("restart with no bound env — cannot re-enter", chunk_id=lease.chunk_id)
            return
        try:
            with self.ctx.tracer.under(step_root(StepKey.attempt(lease.chunk_id, lease.epoch))):
                envelope = self.ctx.hub.get_envelope(lease.chunk_id)
        except (ChunkNotFoundError, ChunkEndedError):
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
        """True iff the hub no longer routes this chunk here, or it is gone outright.

        Unreachable hub → ``False``: a transport failure is never read as a detach. A 404 is the
        one exception — terminal, not something to wait out. Before the runner's first registration
        no route can be judged another's, so it answers ``False`` then too."""
        runner_id = registered_runner_id(self.ctx.identity)
        if runner_id is None:
            return False
        try:
            view = self.ctx.chunk_views.get(self.lease.chunk_id)
        except ChunkNotFoundError:
            return True  # the chunk no longer exists at the hub — terminal, not retryable
        except HubClientError:
            return False  # hub unreachable — last-known directive holds; keep working
        return routed_away(view, runner_id)

    def _braked(self) -> bool:
        """The runner's own brake holds back this attempt's escalations and starts."""
        return not Spawner(self.ctx).brakes().starts_processes

    def close(
        self,
        reason: LeaseChangeCause,
        at: datetime,
        event: dict[str, object] | None = None,
        *,
        closure_reason: str | None = None,
        escalation_cause: EscalationCause | None = None,
    ) -> None:
        """Close this lease. An ``event`` lands in the outbound buffer in the same transaction as the
        closure it describes. Every closure path funnels through here — the one place to pump this lease's
        open transcript segment(s) before ``record_closure`` finalizes them. ``closure_reason`` overrides
        what is recorded, never the published cause; ``escalation_cause`` records why it escalated."""
        self._pump_lease_before_close()
        # Every open invocation boundary closes here too, BEFORE
        # `record_closure` — a crash between the two just retries this idempotent path.
        self.ctx.stores.invocation_boundaries.close_boundaries_for_lease(
            self.lease.lease_id, reason=closure_reason if closure_reason is not None else reason, at=at
        )
        event_seq = self.ctx.stores.lease_record.record_closure(
            lease_id=self.lease.lease_id,
            chunk_id=self.lease.chunk_id,
            node_id=self.lease.node_id,
            reason=closure_reason if closure_reason is not None else reason,
            closed_at=at,
            event_kind=EVENT_RECORDED if event else None,
            event_payload=json.dumps(event) if event else None,
            escalation_cause=escalation_cause,
        )
        # After the closure commits, never before (decision: a crash between the two leaves at
        # most an orphan the startup sweep collects, never a still-active lease's directory gone).
        self.ctx.worker_scratch.remove(self.lease.lease_id)
        if self.ctx.events is not None:
            lease_id = self.lease.lease_id
            # `reason` IS the LeaseChangeCause vocabulary — enforced by `close`'s and
            # `fail`'s own parameter type now, not by a comment's claim about callers.
            self.ctx.events.publish_lease_changed(
                lease_id,
                self.lease.chunk_id,
                cause=reason,
            )
            if reason == LeaseClosureReason.ESCALATED:
                # `open_escalations()`'s derivation — a closed-`escalated` lease not yet
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

    def _kill_in_flight_elicitation(self) -> None:
        """Closing a lease kills its in-flight elicitation, if any — every
        closing path but park (fail, abandon, preempt) reaches here, so no path may leave a
        launched elicitation running against a lease nothing will ever collect. A pause park
        interrupts rather than kills, and leaves the elicitation standing for
        :class:`~blizzard.runner.lifecycle.dormant.DormantSession` to tear down on its own. Its
        output files are swept alongside the record — this is the one place every closing
        path already has the record, with its ``relaunch_count``, in hand."""
        lease = self.lease
        elicitation = self.ctx.stores.elicitations.in_flight_elicitation(lease.lease_id, lease.epoch)
        if elicitation is None:
            return
        # The shared, liveness-checked, pgid-preferring kill `_kill_process` above and
        # takeover's own elicitation teardown both reach through — never a bare pid signal.
        kill_owned_process(
            self.ctx.process,
            pid=elicitation.pid,
            process_start_time=elicitation.process_start_time,
            pgid=elicitation.pgid,
        )
        self.ctx.stores.elicitations.clear_elicitation(lease.lease_id, lease.epoch)
        self.ctx.elicitation_files.cleanup(lease.lease_id, lease.epoch, through_attempt=elicitation.relaunch_count)

    def _pump_lease_before_close(self) -> None:
        """The same best-effort promise :meth:`_kill_process` makes applies here too, weaker:
        exceptions never fail the closure, but delay is bounded, not eliminated — ``deadline`` is checked only BETWEEN
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

    def _resolve_harness(self, session: SessionReference, *, via: str) -> IHarnessWorkerLifecycle | None:
        """Resolve ``session``'s recorded owner, logging and returning ``None`` — never
        raising — when it is unknown or unavailable."""
        try:
            return self.ctx.harnesses.lifecycle(session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            _log.error(
                "attempt step blocked by unavailable harness owner",
                via=via,
                chunk_id=self.lease.chunk_id,
                lease_id=self.lease.lease_id,
                harness_id=session.harness_id,
                detail=str(exc),
            )
            return None

    def _owner_block(self) -> tuple[SessionReference, UnknownHarnessError | UnavailableHarnessError] | None:
        """Whether :meth:`fail`'s own session — if any was ever recorded — has a recorded
        owner this runner cannot dispatch to right now, and the exception that
        says why. ``None`` both when no session exists yet (nothing recorded to check) and
        when the recorded owner resolves fine — the retry branch's own "nothing blocks a
        requeue" case."""
        session = self.lease.session
        if session is None:
            return None
        try:
            self.ctx.harnesses.lifecycle(session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            return session, exc
        return None

    def _detail(self, reason: str, via: str, stderr_tail: str) -> dict[str, object]:
        """The ``(reason, via)`` that classified a :meth:`fail` branch, plus any captured
        stderr tail — every branch's ``detail``, whether it reaches the hub through
        :meth:`_event`'s payload or :class:`OutboundFacts.event`'s own."""
        detail: dict[str, object] = {"via": via, "reason": reason, "node": self.lease.node_name}
        if stderr_tail:
            detail["stderr_tail"] = stderr_tail
        return detail

    def _event(self, kind: EventLogKind, message: str, reason: str, via: str, stderr_tail: str) -> dict[str, object]:
        """The ``event.recorded`` payload one :meth:`fail` branch surfaces."""
        return {
            "severity": EVENT_LOG_SEVERITY[kind],
            "kind": kind,
            "chunk_id": self.lease.chunk_id,
            "lease_id": self.lease.lease_id,
            "node_name": self.lease.node_name,
            "message": message,
            "detail": self._detail(reason, via, stderr_tail),
        }
