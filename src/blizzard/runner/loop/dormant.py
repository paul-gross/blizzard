"""A lease whose worker is gone but whose session survives — parking into it, and waking it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.loop.attempt import Attempt
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.spawn import Spawner
from blizzard.runner.store.repository import AskRecord, EnvBindingRecord, LeaseRecord

_log = get_logger("blizzard.runner.loop")

#: The message RESUME delivers into a marked session on a restart — ``#``-prefixed so it is
#: inert in prose and in a behavior script alike. The exact prose is unpinned.
_RESTART_MESSAGE = "# The supervisor restarted; continue your task where you left off."

#: The message ADVANCE delivers into a session the operator paused and resumed (issue #46).
#: Same inert ``#``-prefixed framing; the exact prose is unpinned.
_UNPAUSE_MESSAGE = "# The operator resumed this chunk; continue your task where you left off."

# The restart re-attach. Its un-recordable middle (a resume whose pid is not yet durable) is
# SPAWN's same by-construction gap; recovery re-runs RESUME idempotently.
_CP_RESUME_AFTER_KILL = crashpoint("resume.after-kill.before-reattach", "survivor killed; session not yet re-attached")
_CP_RESUME_AFTER = crashpoint("resume.after-reattach", "session re-attached under the same lease; intent cleared")


@dataclass(frozen=True)
class DormantSession:
    """A lease with no live worker but a session still resumable under it — parked on a
    question, parked on an operator pause, or marked for restart-resume.

    Every wake here rewrites only ``pid``/``process_start_time``: same lease, same epoch, same
    session, so **no retry is consumed** by going dormant and coming back."""

    ctx: LoopContext
    lease: LeaseRecord

    def resume_on_unmet_produces(self, message: str, bindings: list[EnvBindingRecord]) -> None:
        """Resume a session that exited with required ``produces:`` unattached, instead of
        judging it (issue #422) — no retry consumed, no epoch bumped, the same guarantee every
        dormant-session wake carries. The exited generation's spend is recorded first, the same
        ordering :meth:`park_on_ask` already uses for its own resume-instead-of-judge path."""
        lease = self.lease
        self.ctx.usage.record_worker(lease, bindings)
        pid, _ = self._wake(message, bindings)
        _log.info(
            "resumed premature exit for unmet produces",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            pid=pid,
        )

    def park_on_ask(self, ask: AskRecord) -> None:
        """Park the chunk on a question: forward it to the hub and stop the reap clock.

        The local park fact stops the reap clock and keeps the lease from being re-parked or
        judged; env bindings stay held so the session is warm for the resume."""
        lease = self.lease
        now = self.ctx.clock.now()
        self.ctx.usage.record_worker(lease, self.ctx.store.bindings_for_chunk(lease.chunk_id))
        OutboundFacts(self.ctx).question_asked(lease, ask, at=now)
        self.ctx.store.record_park(
            lease_id=lease.lease_id, chunk_id=lease.chunk_id, question_id=ask.question_id, parked_at=now
        )
        if self.ctx.events is not None:
            # LeaseActivity.state (D4) flips to "parked" — see LeaseChangeCause's own doc
            # (wire/sse_runner.py) for why this cause isn't record_closure's "parked".
            self.ctx.events.publish_lease_changed(
                lease.lease_id,
                lease.chunk_id,
                cause="dormant",
            )
        _log.info("chunk parked on question", chunk_id=lease.chunk_id, question_id=ask.question_id)

    def restart_or_release(self) -> None:
        """Park a paused chunk, else resume in place, else abandon it if the hub reassigned its
        chunk (issue #46), or if the hub no longer knows it at all (blizzard#9).

        The pause branch is **first** and keys on the pause *fact*, not the lossy derived status.
        It is conjoined with ``ours``, so a detached-then-paused chunk still abandons."""
        lease = self.lease
        try:
            detail = self.ctx.hub.get_chunk(lease.chunk_id)
        except ChunkNotFoundError:
            # The chunk is gone outright (e.g. a store reset) — terminal, not retryable; abandon
            # now rather than leave the intent open for PULL's lease reconcile to find later.
            Attempt(self.ctx, lease).abandon(via="resume")
            return
        except HubClientError:
            # Hub unreachable — the intent is durable and the envs stay held. Resuming blind
            # would risk re-asserting authority over a chunk that may have been reassigned.
            return
        ours = detail.route is not None and detail.route.runner_id == self.ctx.config.runner_id
        if ours and detail.pause is not None:
            Attempt(self.ctx, lease).park_paused(via="resume")
        elif detail.status == ChunkStatus.RUNNING and ours:
            self._restart()
        else:
            Attempt(self.ctx, lease).abandon(via="resume")

    def on_answer(self) -> None:
        """Poll a parked lease's question; on an answer, resume the dormant session.

        Crash-safe and re-runnable: an unanswered question polls as a no-op and the reap clock
        stays stopped. Once answered the agent is reconstituted under the same session and step."""
        lease = self.lease
        if Spawner(self.ctx).suppressed(via="answer-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
            return
        park = self.ctx.store.open_park(lease.lease_id)
        if park is None:
            return  # not actually parked (raced with a resume)
        try:
            question = self.ctx.hub.get_question(park.question_id)
        except HubClientError:
            return  # hub unreachable — the park is durable; retry next tick
        if not question.answered or question.answer is None:
            return  # still waiting — reap clock stays stopped
        bindings = self.ctx.store.bindings_for_chunk(lease.chunk_id)
        if not bindings:
            _log.warning("answered park with no bound env — cannot resume", chunk_id=lease.chunk_id)
            return
        # The human framing rides a leading `#` comment line and the answer itself is the
        # payload; the exact prose is unpinned.
        who = question.answered_by or "operator"
        pid, now = self._wake(f"# Answer from {who}. Continue.\n{question.answer}", bindings)
        self.ctx.store.record_park_resume(lease_id=lease.lease_id, question_id=park.question_id, resumed_at=now)
        if self.ctx.events is not None:
            self.ctx.events.publish_ask_changed(
                lease.lease_id,
                lease.chunk_id,
                park.question_id,
                cause="answered",
            )
        OutboundFacts(self.ctx).answer_delivered(lease, park.question_id, at=now)
        _log.info("resumed dormant session with answer", chunk_id=lease.chunk_id, question_id=park.question_id, pid=pid)

    def on_unpause(self) -> None:
        """Poll a pause-parked lease's chunk; once the operator resumes it, restart its session.

        The pause cost the chunk a process, not an attempt. An **ask-parked** lease returns early
        even once unpaused, so a lift never conjures an absent answer (issue #46)."""
        lease = self.lease
        if Spawner(self.ctx).suppressed(via="pause-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
            return
        try:
            detail = self.ctx.hub.get_chunk(lease.chunk_id)
        except ChunkNotFoundError:
            # The chunk is gone outright — not this step's abandon to make; the reconcile sweep
            # owns it and runs ahead of this step in the same tick.
            return
        except HubClientError:
            return  # hub unreachable — the park is durable; retry next tick
        if detail.pause is not None:
            return  # still paused — the reap clock stays stopped
        if detail.route is None or detail.route.runner_id != self.ctx.config.runner_id:
            return  # detached/reassigned while parked — PULL's sweep abandons it, not this step
        now = self.ctx.clock.now()
        if lease.lease_id in self.ctx.store.ask_parked_lease_ids():
            # Dormant on a question underneath the pause: clearing the pause-park is the whole
            # action, and an answer — not this resume — restarts it.
            self.ctx.store.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
            _log.info("pause lifted on an ask-parked chunk — awaiting its answer", chunk_id=lease.chunk_id)
            return
        bindings = self.ctx.store.bindings_for_chunk(lease.chunk_id)
        if not bindings or lease.session_id is None:
            _log.warning("unpaused chunk has no warm env/session — cannot resume", chunk_id=lease.chunk_id)
            return
        pid, _ = self._wake(_UNPAUSE_MESSAGE, bindings, at=now)
        self.ctx.store.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
        _log.info(
            "resumed dormant session after an operator unpause",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            pid=pid,
        )

    def _restart(self) -> None:
        """Kill any survivor, then resume the session under the same lease/epoch/session.

        Kill-first is what prevents two processes on one session — the epoch is not. The brake is
        checked **before the kill**: gating after would kill the survivor and leave it
        un-re-attached."""
        lease = self.lease
        if Spawner(self.ctx).suppressed(via="resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
            return
        now = self.ctx.clock.now()
        if lease.pid is not None:
            self.ctx.process.kill(lease.pid)  # kill-first — never two processes on one session
        _CP_RESUME_AFTER_KILL.reached()  # re-run kills the dead pid (no-op) then re-attaches
        bindings = self.ctx.store.bindings_for_chunk(lease.chunk_id)
        if not bindings or lease.session_id is None:
            _log.warning(
                "marked lease has no warm env/session — abandoning", chunk_id=lease.chunk_id, lease_id=lease.lease_id
            )
            Attempt(self.ctx, lease).abandon(killed=True, via="resume")
            return
        pid, _ = self._wake(_RESTART_MESSAGE, bindings, at=now)
        self.ctx.store.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
        _CP_RESUME_AFTER.reached()  # pid recorded, intent cleared — a crash here re-runs as a no-op
        _log.info(
            "resumed in-flight session after restart",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            epoch=lease.epoch,
            pid=pid,
        )

    def _wake(
        self, message: str, bindings: list[EnvBindingRecord], *, at: datetime | None = None
    ) -> tuple[int, datetime]:
        """Deliver ``message`` into the dormant session, record the new pid under the same lease,
        and return that pid with the instant it was stamped at — an omitted ``at`` reads the
        clock *after* the resume returns, so a caller's later writes share the one instant.

        The resume-with-message → ``record_spawn`` gap is the un-armable spawn-record window: no
        crash point can arm a window whose recovery input (the new pid) does not yet exist."""
        lease = self.lease
        spawner = Spawner(self.ctx)
        pid = self.ctx.harness.resume_with_message(
            bindings[0].workdir,
            lease.session_id or "",
            message,
            stdout_path=spawner.stdout_path(lease.lease_id),
            preamble=spawner.preamble(lease, bindings),
            chunk_id=lease.chunk_id,
            # Reasserted, not sticky (issue #144) — see the judge call site's note.
            effort=lease.resolved_effort,
            # Reasserted, not sticky either (blizzard#343) — mirrors effort's treatment.
            compaction_window=lease.resolved_compaction_window,
        )
        stamped = at if at is not None else self.ctx.clock.now()
        self.ctx.store.record_spawn(
            lease.lease_id,
            pid=pid,
            process_start_time=self.ctx.process.start_time(pid) or "",
            session_id=lease.session_id or "",  # unchanged — same session under the same lease
            spawned_at=stamped,
        )
        if self.ctx.events is not None:
            # Same 'spawned' cause the fresh-spawn path publishes (spawn.py) — a resumed
            # session's own flip back to a live pid is exactly as un-announced otherwise.
            self.ctx.events.publish_lease_changed(
                lease.lease_id,
                lease.chunk_id,
                cause="spawned",
            )
        return pid, stamped
