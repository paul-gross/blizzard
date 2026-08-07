"""The reconciliation step functions — REAP → PULL → FILL → ADVANCE (``bzh:steppable-loop``).

Each is an individually callable function of a :class:`LoopContext`. Every step is
idempotent and holds no state of its own — all facts live in the runner store, so a crash
mid-tick and a restart re-run the tick harmlessly; startup recovery is REAP running first.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.domain.leases import as_utc, is_heartbeat_stale
from blizzard.runner.environments.provider import (
    EnvironmentPreparationError,
    WorkspaceAcquisitionError,
)
from blizzard.runner.harness.external_usage import ExternalSubscriptionUsageSnapshot
from blizzard.runner.harness.spawn_cwd import resolve_spawn_cwd
from blizzard.runner.loop.attempt import (
    FAILED,
    PARKED,
    REAPED,
    TRANSITIONED,
    Attempt,
)
from blizzard.runner.loop.checks import DEFAULT_CHECK_TIMEOUT
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.git_commits import DeclaredCommits
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.judgement_prompt import JudgementPrompt
from blizzard.runner.loop.outbound import COMPLETION_KIND, DECISION_KIND, OutboundFacts
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.loop.produces import ProducesReconciler
from blizzard.runner.loop.spawn import Spawner, environments_for
from blizzard.runner.store.repository import (
    AskRecord,
    BufferedFact,
    CheckResultRecord,
    EnvBindingRecord,
    IWriteRunnerStore,
    LeaseRecord,
)
from blizzard.wire.completion import (
    CheckResult,
    CompletionSubmission,
    SubmittedArtifact,
    checks_gate_violated,
)
from blizzard.wire.decision import DecisionSubmission, DecisionView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeEnvelope
from blizzard.wire.facts import (
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
    RUNNER_LOCALLY_PAUSED,
    RunnerFact,
    RunnerFactBatch,
)
from blizzard.wire.queue import QueuePeekEntry
from blizzard.wire.route import RouteClaim

#: This module's public API — the loop steps it owns.
__all__ = [
    "advance",
    "check_spend_ceiling",
    "fill",
    "flush_outbound",
    "mark_resume_intents",
    "pull",
    "reap",
    "resume",
    "sample_external_subscription_usage",
]

_log = get_logger("blizzard.runner.loop")

#: The message RESUME delivers into a marked session on a restart — ``#``-prefixed so it
#: is inert in prose and in a behavior script alike. The exact prose is unpinned.
_RESTART_RESUME_MESSAGE = "# The supervisor restarted; continue your task where you left off."

#: The message ADVANCE delivers into a session the operator paused and resumed (issue #46).
#: Same inert ``#``-prefixed framing; the exact prose is unpinned.
_PAUSE_RESUME_MESSAGE = "# The operator resumed this chunk; continue your task where you left off."

# The env count a chunk gets when nothing says otherwise — a default, not a structural
# assumption; a chunk holding several is representable.
_DEFAULT_ENV_COUNT = 1

# Crash points (``bzh:crash-point-registry``): armed, each SIGKILLs the tick subprocess at
# the boundary it guards; unarmed, a module-global string compare and a no-op.

# REAP — startup recovery runs this first, so these bracket the recovery pass itself.
_CP_REAP_BEFORE = crashpoint("reap.before-expire", "entered REAP; no lease expired yet")
_CP_REAP_AFTER = crashpoint("reap.after-expire", "REAP done; stale leases expired")

# RESUME — the restart re-attach. Its un-recordable middle (a resume whose pid is not yet
# durable) is SPAWN's same by-construction gap; recovery re-runs RESUME idempotently.
_CP_RESUME_BEFORE = crashpoint("resume.before-reattach", "entered RESUME with marked intents; none re-attached yet")
_CP_RESUME_AFTER_KILL = crashpoint("resume.after-kill.before-reattach", "survivor killed; session not yet re-attached")
_CP_RESUME_AFTER = crashpoint("resume.after-reattach", "session re-attached under the same lease; intent cleared")

# PULL — the single outbound flusher (store-and-forward drain).
_CP_PULL_BEFORE = crashpoint("pull.before-flush", "entered PULL; registry synced, buffer not drained")
_CP_PULL_AFTER = crashpoint("pull.after-flush", "PULL done; buffer drained as far as it could")

# FILL — peek -> acquire -> BIND -> claim -> spawn. The local binding is written *before*
# the hub claim, so a crash in that window is reconciled next tick — never a strand.
_CP_FILL_BEFORE_ACQUIRE = crashpoint("fill.before-env-acquire", "peeked a ready chunk; envs not acquired")
_CP_FILL_AFTER_ACQUIRE = crashpoint("fill.after-env-acquire.before-bind", "envs acquired; binding not recorded")
_CP_FILL_AFTER_BIND = crashpoint("fill.after-bind.before-claim", "binding recorded; route not claimed at the hub")
_CP_FILL_AFTER_CLAIM = crashpoint("fill.after-claim.before-spawn", "hub holds the route; lease not minted")

# ADVANCE — judge an exited worker: verify -> elicit verdict -> buffer completion. Verify
# is read-only, so it needs no crash point of its own (`bzh:crash-correctness` exemption).
_CP_ADV_AFTER_JUDGE = crashpoint("advance.after-judgement.before-buffer", "verdict parsed; completion not buffered")
# Usage recording (issue #58) sits between the verdict and the completion buffer: a crash
# here finds this attempt's usage facts already durable, or neither — never a double-count.
_CP_ADV_AFTER_USAGE = crashpoint("advance.after-usage.before-buffer", "usage facts recorded; completion not buffered")

# ADVANCE's nudge-once (issue #113): the durable `(lease, epoch)` fact is recorded BEFORE
# the resume it guards, so "at most one nudge" holds across a crash at either point.
_CP_NUDGE_AFTER_FIRED_FACT = crashpoint(
    "nudge.after-fired-fact.before-resume",
    "nudge-fired fact durable; the resume that delivers the nudge has not run yet",
)
_CP_NUDGE_AFTER_RESUME = crashpoint(
    "nudge.after-resume.before-reassemble",
    "nudge resume returned; attachments not yet re-read and the completion not yet reassembled",
)

# ADVANCE's checks-at-exit (issue #114): result rows are durable before the marker, so a
# crash between them leaves `checks_ran` unset and recovery safely re-runs (latest-wins).
_CP_CHECKS_AFTER_RESULTS = crashpoint(
    "checks.after-results.before-marker",
    "check result rows durable; the checks-ran marker has not been written yet",
)
_CP_CHECKS_AFTER_MARKER = crashpoint(
    "checks.after-marker.before-judge",
    "checks-ran marker durable; the judgement has not been elicited yet",
)

_CP_ADV_AFTER_BUFFER = crashpoint("advance.after-buffer.before-flush", "completion buffered; not yet flushed")

# The between-attempts boundary the per-chunk spend cap checks at (issue #61a): a crash
# here leaves no active lease and no escalation, recovered by `_reconcile_interrupted_claims`.
_CP_ADV_AFTER_CLOSURE = crashpoint(
    "advance.after-closure.before-cost-cap-check", "attempt closed; cap check and next-step decision not yet made"
)

# FLUSH (of the buffered completion, inside PULL) — submit -> ack -> apply-response. The
# after-submit.before-ack window is the lost-ack replay the hub's idempotency must absorb.
_CP_FLUSH_BEFORE_SUBMIT = crashpoint("flush.before-submit", "completion at head of buffer; not submitted")
_CP_FLUSH_AFTER_SUBMIT = crashpoint("flush.after-submit.before-ack", "hub applied the completion; ack not recorded")
_CP_FLUSH_AFTER_ACK = crashpoint("flush.after-ack.before-apply-response", "ack recorded; apply-response not consumed")
_CP_FLUSH_AFTER_APPLY = crashpoint("flush.after-apply-response", "apply-response consumed; chunk continued in place")


# Usage telemetry (issue #58) — the per-lease stdout redirect and its readback.


# Runner spend ceiling (issue #61b) — the tick-level kill-switch, first in the tick.


def check_spend_ceiling(ctx: LoopContext) -> None:
    """Engage the local pause brake once this runner's rolling-window spend reaches
    ``cost.runner_ceiling_usd``; absent, there is no ceiling (issue #61). Runs **first** in
    the tick so a crossing is visible to every later step in the same pass, engages exactly
    once, and never lifts — only a conscious clear does (tests/test_runner_paused.py)."""
    cap = ctx.config.runner_ceiling_usd
    if cap is None:
        return
    if ctx.store.local_paused(ctx.config.runner_id):
        return  # already engaged — engage-once; `blizzard runner start` is the only clear
    now = ctx.clock.now()
    since = now - timedelta(hours=ctx.config.runner_ceiling_window_hours)
    totals = ctx.store.usage_since(since)
    if totals.cost_usd < cap:
        return
    partial_note = " (PARTIAL — true spend may be higher)" if totals.cost_partial else ""
    window_hours = ctx.config.runner_ceiling_window_hours
    reason = (
        f"spend ceiling ${cap:.2f} reached over the trailing {window_hours:g}h "
        f"(spend ${totals.cost_usd:.2f}{partial_note})"
    )
    _log.warning(
        f"runner locally paused — {reason}",
        runner_id=ctx.config.runner_id,
        ceiling_usd=cap,
        spend_usd=totals.cost_usd,
        window_hours=ctx.config.runner_ceiling_window_hours,
        cost_partial=totals.cost_partial,
    )
    ctx.store.record_local_pause(
        ctx.config.runner_id,
        paused=True,
        at=now,
        by="runner-ceiling",
        report_kind=RUNNER_LOCALLY_PAUSED,
        report_payload=json.dumps(
            {"runner_id": ctx.config.runner_id, "by": "runner-ceiling", "at": iso_utc(now), "reason": reason}
        ),
    )


# REAP


def reap(ctx: LoopContext) -> None:
    """Expire leases whose worker is gone or **stalled** — each a failed attempt.

    An **orphan** (minted but never spawned) and a **stalled-but-alive** worker end here.
    An exited session-bearing worker is ADVANCE's: exit is the done declaration, and the
    conservative staleness threshold is what keeps the two apart."""
    _CP_REAP_BEFORE.reached()
    local_paused = ctx.store.local_paused(ctx.config.runner_id)
    now = ctx.clock.now()
    parked = ctx.store.parked_lease_ids()
    taken_over = ctx.store.open_takeover_chunk_ids()
    deferred = 0
    for lease in ctx.store.list_active_leases():
        if lease.chunk_id in taken_over:
            continue  # the human holds this session — no loop step touches it
        if lease.lease_id in parked:
            # Dormant on a question: no live worker to stall, so the reap clock is
            # stopped — a parked chunk is never reaped for inactivity.
            continue
        if lease.pid is None or lease.session_id is None:
            _log.info("reaping unspawned lease", lease_id=lease.lease_id, chunk_id=lease.chunk_id)
            Attempt(ctx, lease).fail(reason=REAPED, via="reap")
            continue
        if not ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # exited — ADVANCE's (exit-is-done)
        if is_heartbeat_stale(ctx.store, lease, now):
            if local_paused:
                # Do not kill a live worker while the brake is on — a pause is not a
                # drain. The first tick after it clears reaps this lease as it would now.
                deferred += 1
                continue
            _log.info("reaping stalled worker", lease_id=lease.lease_id, chunk_id=lease.chunk_id, pid=lease.pid)
            Attempt(ctx, lease).fail(reason=REAPED, via="reap")
        # A live, beating worker runs on.
    if deferred:
        _log.info("reap deferred — locally paused", runner_id=ctx.config.runner_id, count=deferred)
    _CP_REAP_AFTER.reached()


# RESUME — the restart re-attach: graceful marking (#12) + crash detection (#13)


def mark_resume_intents(store: IWriteRunnerStore, *, now: datetime) -> int:
    """Mark every in-flight lease for same-lease restart-resume — the graceful-shutdown hook.

    Marks an active, non-parked, session-bearing lease and returns the count. Store-only, and
    one durable row per mark, so a crash mid-marking degrades to the ungraceful path.
    """
    parked = store.parked_lease_ids()
    pending = store.pending_submission_lease_ids()
    marked = 0
    for lease in store.list_active_leases():
        if lease.pid is None or lease.session_id is None:
            continue
        if lease.lease_id in parked or lease.lease_id in pending:
            continue
        store.record_resume_intent(lease_id=lease.lease_id, marked_at=now)
        marked += 1
    if marked:
        _log.info("marked in-flight leases for restart-resume", count=marked)
    return marked


def mark_crash_resume_intents(store: IWriteRunnerStore, *, process: IProcessProbe, now: datetime) -> int:
    """Detect crash-orphaned sessions at startup and mark them for same-lease resume (#13).

    Staleness is measured against :meth:`last_daemon_liveness`, not the clock at recovery,
    which at startup is ``downtime + idle-at-crash``. Returns the number marked.
    """
    parked = store.parked_lease_ids()
    pending = store.pending_submission_lease_ids()
    ended = store.session_ended_lease_ids()
    # as_utc: this instant is about to be subtracted from, and a naive one compares wrong.
    last_alive = store.last_daemon_liveness()
    crashed_at = as_utc(last_alive) if last_alive is not None else now
    marked = 0
    for lease in store.list_active_leases():
        if lease.pid is None or lease.session_id is None:
            continue  # never reached spawn-return — REAP's residue, nothing to resume
        if lease.lease_id in parked or lease.lease_id in pending:
            continue  # dormant on a question / outcome already elicited — not a crash to resume
        if lease.lease_id in ended:
            continue  # declared done (SessionEnd fired) — ADVANCE judges it (exit-is-done)
        if process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # orphaned-but-alive — re-adopted via its live heartbeat, never re-spawned
        if is_heartbeat_stale(store, lease, crashed_at):
            continue  # stalled at crash time — reaped & retried per the node's budget, unchanged
        store.record_resume_intent(lease_id=lease.lease_id, marked_at=now)
        marked += 1
    if marked:
        _log.info("marked crash-interrupted leases for restart-resume", count=marked)
    return marked


def resume(ctx: LoopContext) -> None:
    """Re-attach to in-flight sessions a restart marked for same-lease resume — startup recovery.

    Each marked lease is either resumed in place — unchanged ``lease_id``/``epoch``/
    ``session_id``, no retry consumed — or abandoned with no epoch bump. Runs before ADVANCE so a
    resumed lease reads live again by the time ADVANCE iterates."""
    intents = ctx.store.resume_intent_lease_ids()
    if not intents:
        return
    _CP_RESUME_BEFORE.reached()  # marked intents present; a crash here re-runs RESUME unchanged
    active = {lease.lease_id: lease for lease in ctx.store.list_active_leases()}
    for lease_id in intents:
        lease = active.get(lease_id)
        if lease is None:
            ctx.store.record_resume_clear(lease_id=lease_id, cleared_at=ctx.clock.now())
            continue
        _resume_marked_lease(ctx, lease)


def _resume_marked_lease(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Park a paused chunk, else resume in place, else abandon it if the hub reassigned its chunk
    (issue #46), or if the hub no longer knows it at all (blizzard#9).

    The pause branch is **first** and keys on the pause *fact*, not the lossy derived status. It
    is conjoined with ``ours``, so a detached-then-paused chunk still abandons."""
    try:
        detail = ctx.hub.get_chunk(lease.chunk_id)
    except ChunkNotFoundError:
        # The chunk is gone outright (e.g. a store reset) — terminal, not retryable; abandon now
        # rather than leave the intent open for PULL's `_reconcile_leases` to find it later.
        Attempt(ctx, lease).abandon(via="resume")
        return
    except HubClientError:
        # Hub unreachable — the intent is durable and the envs stay held. Resuming blind
        # would risk re-asserting authority over a chunk that may have been reassigned.
        return
    ours = detail.route is not None and detail.route.runner_id == ctx.config.runner_id
    if ours and detail.pause is not None:
        Attempt(ctx, lease).park_paused(via="resume")
    elif detail.status == ChunkStatus.RUNNING and ours:
        _resume_in_place(ctx, lease)
    else:
        Attempt(ctx, lease).abandon(via="resume")


def _resume_in_place(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Kill any survivor, then resume the session under the same lease/epoch/session.

    Kill-first is what prevents two processes on one session — the epoch is not. Only
    ``pid``/``process_start_time`` are rewritten, so no retry is consumed. The brake is checked
    **before the kill**: gating after would kill the survivor and leave it un-re-attached."""
    if Spawner(ctx).suppressed(via="resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return
    now = ctx.clock.now()
    if lease.pid is not None:
        ctx.process.kill(lease.pid)  # kill-first — never two processes on one session
    _CP_RESUME_AFTER_KILL.reached()  # survivor killed; re-run kills the dead pid (no-op) then re-attaches
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings or lease.session_id is None:
        _log.warning(
            "marked lease has no warm env/session — abandoning", chunk_id=lease.chunk_id, lease_id=lease.lease_id
        )
        Attempt(ctx, lease).abandon(killed=True, via="resume")
        return
    # The resume-with-message -> record_spawn gap is the un-armable spawn-record window: no
    # crash point can arm a window whose recovery input (the new pid) does not yet exist.
    pid = ctx.harness.resume_with_message(
        bindings[0].workdir,
        lease.session_id,
        _RESTART_RESUME_MESSAGE,
        stdout_path=Spawner(ctx).stdout_path(lease.lease_id),
        preamble=Spawner(ctx).preamble(lease, bindings),
        chunk_id=lease.chunk_id,
        # Reasserted, not sticky (issue #144) — see the judge call site's note.
        effort=lease.resolved_effort,
    )
    ctx.store.record_spawn(
        lease.lease_id,
        pid=pid,
        process_start_time=ctx.process.start_time(pid) or "",
        session_id=lease.session_id,  # unchanged — same session under the same lease
        spawned_at=now,
    )
    ctx.store.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
    _CP_RESUME_AFTER.reached()  # pid recorded, intent cleared — a crash here re-runs RESUME as a no-op
    _log.info(
        "resumed in-flight session after restart",
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        epoch=lease.epoch,
        pid=pid,
    )


# PULL


def pull(ctx: LoopContext) -> None:
    """Exchange facts with the hub: sync the registry, reconcile ownership, drain the buffer.

    Store-and-forward always: this is the single flusher, draining FIFO so a fact never
    overtakes a stuck earlier one. A transport failure stops the drain until the next tick.
    """
    _sync_registry(ctx)
    _reconcile_leases(ctx)
    _CP_PULL_BEFORE.reached()
    flush_outbound(ctx)
    _CP_PULL_AFTER.reached()


def _sync_registry(ctx: LoopContext) -> None:
    """Register + heartbeat and mirror the hub's pause brake locally.

    Registration is idempotent and doubles as the runner-level liveness heartbeat. The pause
    brake is mirrored locally, and an unreachable hub leaves the last mirrored value standing.
    """
    try:
        ctx.hub.register_runner(
            ctx.config.runner_id,
            ctx.config.workspace_id,
            env_capacity=ctx.config.env_capacity,
            url=ctx.config.public_url or None,
            redirect_uris=ctx.config.redirect_uris,
        )
        paused = ctx.hub.fetch_runner_paused(ctx.config.runner_id)
    except HubClientError:
        return  # hub unreachable — keep the last-mirrored brake
    ctx.store.set_hub_paused(ctx.config.runner_id, paused=paused, at=ctx.clock.now())


def _reconcile_leases(ctx: LoopContext) -> None:
    """Reconcile every active lease against the hub's view of its chunk — abandon it if the hub
    no longer routes it here, else park it if the operator paused it (issue #46).

    Both questions share **one** ``get_chunk`` per lease, and a transport failure is never read
    as a detach or a pause. The pause branch keys on the pause *fact*, which an ask-park masks."""
    pause_parked = ctx.store.pause_parked_lease_ids()  # hoisted: the park guard, one read per tick
    for lease in ctx.store.list_active_leases():
        try:
            detail = ctx.hub.get_chunk(lease.chunk_id)
        except ChunkNotFoundError:
            # Terminal, not retryable (blizzard#9). Ordered before the HubClientError arm
            # because it subclasses it, or the 404 would be swallowed as "hub unreachable".
            Attempt(ctx, lease).abandon(via="pull")
            continue
        except HubClientError:
            continue  # hub unreachable — last-known directive holds; keep working
        if detail.status == ChunkStatus.STOPPED:
            # Honor the terminal fact directly (issue #118), rather than waiting on the
            # route check below to observe the release.
            Attempt(ctx, lease).abandon(via="pull")
        elif detail.route is None or detail.route.runner_id != ctx.config.runner_id:
            Attempt(ctx, lease).abandon(via="pull")
        elif detail.pause is not None and lease.lease_id not in pause_parked:
            Attempt(ctx, lease).park_paused(via="pull")


def flush_outbound(ctx: LoopContext) -> None:
    """Drain the outbound buffer in FIFO order until a fact fails to deliver."""
    for fact in ctx.store.pending_outbound():
        if not _flush_one(ctx, fact):
            break  # transport failure — stop; strict FIFO, retry the backlog next tick


def _flush_one(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Deliver one buffered fact. Return False on a transport failure (stop the drain)."""
    if fact.kind == COMPLETION_KIND:
        return _flush_completion(ctx, fact)
    if fact.kind == DECISION_KIND:
        return _flush_decision(ctx, fact)
    return _flush_hub_fact(ctx, fact)


def _flush_hub_fact(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Push one buffered fact to POST /events — the generic default arm for every kind
    but completion and decision."""
    payload = json.loads(fact.payload)
    batch = RunnerFactBatch(
        runner_id=ctx.config.runner_id,
        facts=[RunnerFact(seq=fact.seq, kind=fact.kind, payload=payload)],
    )
    try:
        ack = ctx.hub.push_facts(batch)
    except HubClientError:
        return False  # hub unreachable — the fact stays buffered, retried next tick
    if fact.seq in ack.rejected:
        # A contract rejection is not idempotency — surface it, but do not wedge the FIFO
        # drain on a fact the hub will never accept: ack and move on.
        _log.error("hub rejected buffered fact", seq=fact.seq, kind=fact.kind)
    ctx.store.ack_outbound(fact.seq, acked_at=ctx.clock.now())
    return True


def _flush_completion(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Submit a buffered completion and drive its apply-response.

    Idempotent by construction: the apply is epoch-idempotent, and the response is acted on
    only while the lease is still active, so a re-flush past a lost ack just clears the buffer.
    """
    payload = json.loads(fact.payload)
    submission = CompletionSubmission.model_validate(payload["submission"])
    _CP_FLUSH_BEFORE_SUBMIT.reached()
    try:
        response = ctx.hub.submit_completion(fact.chunk_id or "", submission)
    except HubClientError:
        return False  # completion stays durable in the buffer; the mid-node worker is unaffected

    _CP_FLUSH_AFTER_SUBMIT.reached()  # hub applied it; a crash here is the lost-ack replay
    ctx.store.ack_outbound(fact.seq, acked_at=ctx.clock.now())
    _CP_FLUSH_AFTER_ACK.reached()
    lease = ctx.store.active_lease(fact.lease_id or "")
    if lease is None:
        # Already advanced on an earlier flush whose ack was lost — nothing to do.
        return True
    _consume_apply_response(ctx, lease, response)
    _CP_FLUSH_AFTER_APPLY.reached()
    return True


def _flush_decision(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Submit a buffered runner-config gate decision and park the chunk.

    There is no next envelope to continue into, so the flush closes the lease and holds the
    environments. The apply is natural-key idempotent, so a re-flush just clears the buffer.
    """
    payload = json.loads(fact.payload)
    submission = DecisionSubmission.model_validate(payload["submission"])
    try:
        response = ctx.hub.submit_decision(fact.chunk_id or "", submission)
    except HubClientError:
        return False  # decision stays durable in the buffer; retried next tick

    ctx.store.ack_outbound(fact.seq, acked_at=ctx.clock.now())
    lease = ctx.store.active_lease(fact.lease_id or "")
    if lease is None:
        return True  # already parked on an earlier flush whose ack was lost
    if response.outcome == ApplyOutcome.FAILURE:
        _log.warning("decision rejected on flush", chunk_id=lease.chunk_id, detail=response.detail or "")
        Attempt(ctx, lease).fail(reason=FAILED, via="pull")
        return True
    ctx.store.record_closure(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        node_id=lease.node_id,
        reason=PARKED,
        closed_at=ctx.clock.now(),
    )
    _log.info("chunk parked at runner-config gate", chunk_id=lease.chunk_id, node=lease.node_name)
    return True


def _consume_apply_response(ctx: LoopContext, lease: LeaseRecord, response: ApplyResponse) -> None:
    """Record the closure and continue in place per the hub's apply-response.

    Between the closure below and any next-attempt spawn sits the boundary the per-chunk spend
    cap checks at: the attempt just closed is genuinely done, so parking here kills nothing live.
    """
    if response.outcome == ApplyOutcome.FAILURE:
        # A semantic rejection — a stale-epoch or terminal completion. The attempt failed;
        # requeue or escalate. The chunk never advanced.
        _log.warning("completion rejected on flush", chunk_id=lease.chunk_id, detail=response.detail or "")
        Attempt(ctx, lease).fail(reason=FAILED, via="pull")
        return
    now = ctx.clock.now()
    ctx.store.record_closure(
        lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=TRANSITIONED, closed_at=now
    )
    _CP_ADV_AFTER_CLOSURE.reached()
    if response.outcome == ApplyOutcome.NEXT and _park_on_cost_cap(ctx, lease):
        return  # capped — needs_human; the next attempt is not spawned
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    _apply_response(ctx, lease.chunk_id, response.outcome, response.next_envelope, bindings)


def _park_on_cost_cap(ctx: LoopContext, lease: LeaseRecord) -> bool:
    """True — chunk parked ``needs_human`` — iff its spend has reached ``cost.chunk_cap_usd``.

    Reads the hub-derived total (``bzh:facts-not-status``), never a local sum. That total is a
    LOWER BOUND — a cost-absent row contributes $0 — so the cap trips conservatively.
    """
    cap = ctx.config.chunk_cap_usd
    if cap is None:
        return False
    try:
        detail = ctx.hub.get_chunk(lease.chunk_id)
    except HubClientError:
        return False  # hub unreachable — re-checked at the next step boundary
    cost = detail.cost
    if cost.cost_usd < cap:
        return False
    partial_note = " (PARTIAL — true spend may be higher)" if cost.cost_partial else ""
    _log.warning(
        f"chunk parked — spend cap exceeded{partial_note}",
        chunk_id=lease.chunk_id,
        cap_usd=cap,
        spend_usd=cost.cost_usd,
        cost_partial=cost.cost_partial,
    )
    Attempt(ctx, lease).escalate(reason=f"spend cap ${cap:.2f} reached (spend ${cost.cost_usd:.2f}{partial_note})")
    return True


# FILL


def fill(ctx: LoopContext) -> None:
    """Keep the fleet busy: peek → acquire → claim-by-route → bind → spawn.

    Open slots are ``MAX_AGENTS - active_leases``; for each, peek the ready queue, acquire the
    chunk's environments all-or-nothing, and claim the route. Either brake stops *new* claims.
    """
    _reconcile_interrupted_claims(ctx)
    hub_paused = ctx.store.hub_paused(ctx.config.runner_id)
    local_paused = ctx.store.local_paused(ctx.config.runner_id)
    if hub_paused or local_paused:
        _log.info(
            "paused — no new claims this tick",
            runner_id=ctx.config.runner_id,
            hub_paused=hub_paused,
            local_paused=local_paused,
        )
        return
    slots = ctx.config.max_agents - len(ctx.store.list_active_leases())
    for _ in range(max(slots, 0)):
        if not _fill_one(ctx):
            break


def _reconcile_interrupted_claims(ctx: LoopContext) -> None:
    """Reconcile bindings left by a crash in FILL's bind→claim→spawn window.

    The binding is written locally *before* the hub claim, so a crash in that window leaves a
    binding for a chunk with no active lease. Runs before FILL peeks new work: adopt a route
    still ours, else release the orphaned binding."""
    requeue_pending = ctx.store.pending_requeue_chunk_ids()  # hoisted: one read per FILL, not per chunk
    for chunk_id in ctx.store.live_tenure_chunk_ids():
        if ctx.store.active_lease_for_chunk(chunk_id) is not None:
            continue  # a live worker holds it — REAP/ADVANCE own it
        try:
            detail = ctx.hub.get_chunk(chunk_id)
        except ChunkNotFoundError:
            _log.warning("hub reports interrupted-claim chunk unknown — releasing envs", chunk_id=chunk_id)
            ctx.env_release.release_chunk(chunk_id)
            continue
        except HubClientError:
            continue  # hub unreachable — the binding is durable; retry next tick
        if chunk_id in requeue_pending:
            # An explicit human decision (issue #53) outranks every other branch below —
            # nothing here should second-guess it.
            ours = detail.route is not None and detail.route.runner_id == ctx.config.runner_id
            if not ours:
                _log.info("releasing binding — chunk requeued locally but no longer routed here", chunk_id=chunk_id)
                ctx.env_release.release_chunk(chunk_id)
                continue
            _resume_requeued_chunk(ctx, chunk_id)
            continue
        if detail.decision is not None:
            # A resolved gate keeps its route live, so it looks exactly like an interrupted
            # claim; without this guard the adopt branch would bump the epoch under the human.
            continue
        bindings = ctx.store.bindings_for_chunk(chunk_id)
        if not bindings:
            continue
        ours = detail.route is not None and detail.route.runner_id == ctx.config.runner_id
        if detail.status == ChunkStatus.RUNNING and ours:
            _adopt_interrupted_claim(ctx, chunk_id)  # route ours — just spawn the current node
        elif detail.status == ChunkStatus.READY:
            _reclaim_interrupted(ctx, chunk_id, bindings)  # claim never landed — claim now, reuse the binding
        elif detail.route is not None and not ours:
            _log.info("releasing binding — another runner won the chunk", chunk_id=chunk_id)
            ctx.env_release.release_chunk(chunk_id)
        elif detail.route is None:
            # No live route, and neither claimable nor ours to adopt (blizzard#202). Release
            # explicitly instead of matching no branch and leaking the binding forever.
            _log.info(
                "releasing binding — hub reports no live route in a non-ready, non-running state",
                chunk_id=chunk_id,
                hub_status=str(detail.status),
            )
            ctx.env_release.release_chunk(chunk_id)


def _environments_wanted(entry: QueuePeekEntry) -> int:
    """How many environments this queue entry's chunk should be acquired.

    The single place the count is decided, so raising it above one is a change here rather
    than an audit of everything that assumed a lone binding."""
    del entry  # no per-chunk demand signal exists yet
    return _DEFAULT_ENV_COUNT


def _fill_one(ctx: LoopContext) -> bool:
    """Claim and start one chunk. Returns False when nothing more can be filled."""
    try:
        peek = ctx.hub.peek_queue()
    except HubClientError:
        return False  # hub unreachable — try next tick
    if not peek.entries:
        return False

    entry = peek.entries[0]
    held = ctx.store.held_environment_ids()
    _CP_FILL_BEFORE_ACQUIRE.reached()
    try:
        acquired = ctx.provider.acquire(entry.chunk_id, _environments_wanted(entry), held)
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
        OutboundFacts(ctx).command_failed(
            chunk_id=entry.chunk_id,
            lease_id=None,
            node_name=None,
            command=f"winter env-prep step: {exc.step}",
            stderr_tail=str(exc),
        )
        return False
    except WorkspaceAcquisitionError:
        _log.info("acquire refused — env-bound this tick", chunk_id=entry.chunk_id)
        return False  # env capacity exhausted; the chunk waits

    # Bind locally BEFORE claiming at the hub: without a local trace, a crash after a won
    # claim would strand the chunk with nothing on this side to drive or reap.
    _CP_FILL_AFTER_ACQUIRE.reached()
    now = ctx.clock.now()
    for a in acquired:
        ctx.store.record_binding(
            chunk_id=entry.chunk_id, environment_id=a.environment_id, workdir=a.workdir, bound_at=now
        )
    _CP_FILL_AFTER_BIND.reached()

    claim = RouteClaim(
        chunk_id=entry.chunk_id,
        runner_id=ctx.config.runner_id,
        workspace_id=ctx.config.workspace_id,
        environment_ids=[a.environment_id for a in acquired],
    )
    try:
        outcome = ctx.hub.claim_route(claim)
    except HubClientError:
        # Ambiguous — the claim may or may not have committed. Releasing the binding here
        # could strand the chunk, so leave it; the next tick resolves it authoritatively.
        return False
    if outcome.denied_paused is not None:
        # Refused outright, not beaten in the race (issue #44) — stop filling this tick
        # rather than burn the remaining slots on claims that will be refused the same way.
        _log.info(
            "route claim denied — runner paused at the hub", chunk_id=entry.chunk_id, runner_id=ctx.config.runner_id
        )
        ctx.env_release.release_binding(entry.chunk_id, acquired)
        return False
    if outcome.denied_terminal is not None:
        # The chunk reached a terminal state between this peek and this claim (issue #118)
        # — not a race loss. Undo the binding and move on; it cannot be peeked again.
        _log.info(
            "route claim denied — chunk is terminal",
            chunk_id=entry.chunk_id,
            status=outcome.denied_terminal.status,
        )
        ctx.env_release.release_binding(entry.chunk_id, acquired)
        return True  # peek fresh next iteration
    if outcome.conflict is not None or outcome.claimed is None:
        _log.info("route claim lost the race", chunk_id=entry.chunk_id)
        ctx.env_release.release_binding(entry.chunk_id, acquired)  # someone else won — undo our binding
        return True  # peek fresh next iteration

    _CP_FILL_AFTER_CLAIM.reached()
    # Stash the won claim's plaintext route token (issue #84a) before spawning: every later
    # reader takes it out of the store, never off `outcome.claimed` directly.
    ctx.store.set_route_token(entry.chunk_id, token=outcome.claimed.route_token, at=ctx.clock.now())
    resume_from = ctx.sessions.resume_target(
        entry.chunk_id,
        outcome.claimed.envelope.node,
        resolve_spawn_cwd(ctx.config.workspace_root, acquired[0].workdir if acquired else None),
    )
    Spawner(ctx).spawn(entry.chunk_id, outcome.claimed.envelope, acquired, via="fill", resume_from=resume_from)
    return True


# ADVANCE


def advance(ctx: LoopContext) -> None:
    """Judge finished workers and move chunks through the graph.

    Two responsibilities: an exited session-bearing worker is a done declaration to judge and
    buffer, and a held chunk with no active lease is driven separately.
    """
    pending = ctx.store.pending_submission_lease_ids()
    ask_parked = ctx.store.ask_parked_lease_ids()
    pause_parked = ctx.store.pause_parked_lease_ids()
    resume_intents = ctx.store.resume_intent_lease_ids()
    taken_over = ctx.store.open_takeover_chunk_ids()
    for lease in ctx.store.list_active_leases():
        if lease.chunk_id in taken_over:
            continue  # the human holds this session — no loop step touches it
        if lease.pid is None or lease.session_id is None:
            continue  # REAP's residue
        if lease.lease_id in resume_intents:
            continue  # RESUME hasn't re-attached (or abandoned) it yet — not exited work
        if lease.lease_id in pending:
            continue  # outcome elicited, awaiting flush — the node boundary
        if lease.lease_id in pause_parked:
            _resume_if_unpaused(ctx, lease)  # dormant on an operator pause — resume when it lifts
            continue
        if lease.lease_id in ask_parked:
            _resume_if_answered(ctx, lease)  # dormant on a question — resume on the answer
            continue
        if ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # worker still running
        _advance_exited_worker(ctx, lease)

    for chunk_id in ctx.store.live_tenure_chunk_ids():
        if chunk_id in taken_over:
            continue  # the human holds this chunk — no gate/hub-node poll while they do
        if ctx.store.active_lease_for_chunk(chunk_id) is None:
            _advance_held_chunk(ctx, chunk_id)


def _run_or_read_checks(
    ctx: LoopContext, lease: LeaseRecord, envelope: NodeEnvelope, bindings: list[EnvBindingRecord]
) -> list[CheckResultRecord]:
    """Run the node's ``checks:`` at worker exit, or read the results back (issue #114).

    Rows are recorded before the marker, which is what makes them exactly-once across a crash.
    The re-run key is ``(lease, epoch)``, so a retry re-runs against the rebuilt tree.
    """
    node = envelope.node
    if not node.checks:
        return []
    if ctx.store.checks_ran(lease.lease_id, lease.epoch):
        return ctx.store.check_results_for_lease(lease.lease_id, lease.epoch)
    if ctx.check_runner is None:
        # The seam is unwired but the node declares checks — a wiring bug, never a production
        # path. Surface it loudly and skip rather than wedge the tick.
        _log.error(
            "node declares checks but no check-runner seam is wired — skipping checks",
            node=node.node_name,
            lease_id=lease.lease_id,
        )
        return []
    cwd = os.path.join(bindings[0].workdir, node.checks_cwd) if node.checks_cwd else bindings[0].workdir
    timeout = node.checks_timeout or DEFAULT_CHECK_TIMEOUT
    results: list[CheckResultRecord] = []
    for command in node.checks:
        outcome = ctx.check_runner.run(command, cwd, timeout)
        results.append(CheckResultRecord(command=command, passed=outcome.passed, output_tail=outcome.output_tail))
    # Rows first, then the marker — what `runner:checks-recorded-when-marked` rests on.
    ctx.store.record_check_results(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        node_id=lease.node_id,
        epoch=lease.epoch,
        results=results,
        at=ctx.clock.now(),
    )
    _CP_CHECKS_AFTER_RESULTS.reached()
    ctx.store.record_checks_ran(lease_id=lease.lease_id, epoch=lease.epoch, at=ctx.clock.now())
    _CP_CHECKS_AFTER_MARKER.reached()
    _log.info(
        "checks executed",
        node=node.node_name,
        count=len(results),
        red=sum(1 for r in results if not r.passed),
        lease_id=lease.lease_id,
    )
    return results


def _advance_exited_worker(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Park on an open ask, else elicit the verdict and buffer the completion.

    The judgement elicitation is itself a spawn, so the local brake gates it (issue #45) —
    placed low here, since the branches above it start no process.
    """
    if lease.session_id is None:
        return  # not spawned — REAP's residue (guarded by the caller too)

    # Ask-and-exit: an exit holding an unforwarded ask is a park, an exit with neither is a
    # failure. Not a spawn, so it proceeds regardless of the local brake.
    ask = ctx.store.unforwarded_ask(lease.lease_id)
    if ask is not None:
        _park_on_ask(ctx, lease, ask)
        return

    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings:
        _log.warning("exited worker with no bound env — skipping", chunk_id=lease.chunk_id)
        return

    try:
        envelope = ctx.hub.get_envelope(lease.chunk_id)
    except HubClientError:
        return  # hub unreachable — the worker's exit is durable; retry next tick

    commits = DeclaredCommits(ctx, lease, bindings)
    artifacts = commits.verify()

    # 1b. This operator gates this node by name, so the outcome is a human's: buffer a
    #      Decision instead of eliciting a verdict. Not a spawn, so it is ungated.
    if lease.node_name in ctx.config.gates:
        _buffer_decision(ctx, lease, artifacts)
        return

    # 2. Elicit the verdict via the judgement resume — a spawn primitive, gated here rather
    #    than at the top so the non-spawn work above still happens while paused (issue #45).
    if Spawner(ctx).suppressed(via="advance", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return

    # 1c. Run the node's `checks:` before the judgement (issue #114), against the tree the
    #      worker just left — the same tree its judgement and the gate are rendered on.
    check_records = _run_or_read_checks(ctx, lease, envelope, bindings)

    prompt = JudgementPrompt(envelope, check_records).render()
    # The judgement turn carries a re-minted lease identity; the worker is already dead,
    # so invalidating its token orphans nothing.
    output = ctx.harness.judge(
        bindings[0].workdir,
        lease.session_id,
        prompt,
        preamble=Spawner(ctx).preamble(lease, bindings),
        chunk_id=lease.chunk_id,
        # Reassert the stamped effort (issue #144): effort is NOT session-sticky, so a
        # resume that omits it drops the declared value back to the ambient default.
        effort=lease.resolved_effort,
        model=lease.resolved_model,
    )

    # 2c. Record this attempt's harness usage (issue #58) *before* the verdict is parsed, so
    #      a verdict-less fail does not discard the spend the attempt genuinely burned.
    ctx.usage.record_attempt(lease, bindings, judge_output=output)

    choice = ctx.harness.parse_verdict(output)
    if choice is None:
        _log.warning("verdict-less judgement — failing attempt", chunk_id=lease.chunk_id, lease_id=lease.lease_id)
        Attempt(ctx, lease).fail(reason=FAILED, via="advance")
        return
    _CP_ADV_AFTER_JUDGE.reached()
    _CP_ADV_AFTER_USAGE.reached()

    # The checks gate (issue #114) is evaluated BEFORE the nudge, so it judges the exact
    # checks the worker was shown — gate and worker can never diverge on "the tree".
    selected = next((c for c in envelope.node.choices if c.name == choice), None)
    if selected is not None and checks_gate_violated(selected.requires_checks, check_records):
        _log.warning(
            "requires_checks choice selected with a red check — failing attempt",
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            choice=choice,
        )
        Attempt(ctx, lease).fail(reason=FAILED, via="advance")
        return

    # 2a. Nudge-once (issue #113): the guard fact is recorded BEFORE the resume, which is what
    #      makes "at most one nudge per (lease, epoch)" hold across a kill -9 at either point.
    assessment = ctx.harness.parse_assessment(output)
    attachments = ctx.store.attachments_for_lease(lease.lease_id)
    produces = ProducesReconciler(envelope)
    missing = produces.missing(artifacts, attachments)
    if missing and not ctx.store.nudge_fired(lease.lease_id, lease.epoch):
        _log.warning(
            "nudging worker for unattached produces names",
            node=envelope.node.node_name,
            missing=[spec.name for spec in missing],
            lease_id=lease.lease_id,
            epoch=lease.epoch,
        )
        ctx.store.record_nudge_fired(lease_id=lease.lease_id, epoch=lease.epoch, at=ctx.clock.now())
        _CP_NUDGE_AFTER_FIRED_FACT.reached()
        # `judge`, not `resume_with_message`: the reply is discarded, but the resume must be
        # *synchronous* or the attachments re-read below races the worker still attaching.
        nudge_output = ctx.harness.judge(
            bindings[0].workdir,
            lease.session_id,
            produces.nudge_message(missing),
            preamble=Spawner(ctx).preamble(lease, bindings),
            chunk_id=lease.chunk_id,
            effort=lease.resolved_effort,
            model=lease.resolved_model,
        )
        _CP_NUDGE_AFTER_RESUME.reached()
        # A distinct `nudge` kind (issue #58) so it cannot collide with the primary
        # judgement's own fact at this same generation.
        nudge_generation = ctx.store.lease_generation(lease.lease_id)
        nudge_sample = ctx.harness.parse_usage(nudge_output, "nudge", model=lease.resolved_model)
        if nudge_sample is not None:
            ctx.usage.record_sample(lease, generation=nudge_generation, sample=nudge_sample)
        # Re-read: a worker that attached during the nudge must have its content picked
        # up before assembly below, not the assessment fallback it just corrected.
        attachments = ctx.store.attachments_for_lease(lease.lease_id)
        artifacts = commits.amend(artifacts)

    # 2b. Harvest asset artifacts for any `produces` name no git commit covers, read from
    #      the durable store so a restart between attach and completion still sees it.
    artifacts += produces.collect_assets(artifacts, assessment, attachments)

    # 3. Buffer the completion — one atomic, epoch-fenced write. The entry names the lease,
    #    so ADVANCE skips it until the flush closes it.
    submission = CompletionSubmission(
        choice=choice,
        epoch=lease.epoch,
        runner_id=ctx.config.runner_id,
        from_node_id=lease.node_id,
        # `(command, passed)` only — `output_tail` stays runner-local, off the wire.
        check_results=[CheckResult(command=r.command, passed=r.passed) for r in check_records],
        artifacts=artifacts,
        route_token=ctx.store.route_token(lease.chunk_id),  # issue #84a — stamped at enqueue
    )
    OutboundFacts(ctx).completion(lease, submission, at=ctx.clock.now())
    _CP_ADV_AFTER_BUFFER.reached()
    _log.info("completion buffered", chunk_id=lease.chunk_id, lease_id=lease.lease_id, choice=choice)


def _buffer_decision(ctx: LoopContext, lease: LeaseRecord, artifacts: list[SubmittedArtifact]) -> None:
    """Buffer a runner-config gate decision — the gated node-step's outcome.

    The choice set is not the runner's, so the submission carries only the step's artifacts
    and its fence; ADVANCE skips this lease until the flush closes it.
    """
    submission = DecisionSubmission(
        from_node_id=lease.node_id,
        epoch=lease.epoch,
        runner_id=ctx.config.runner_id,
        artifacts=artifacts,
        route_token=ctx.store.route_token(lease.chunk_id),  # issue #84a — stamped at enqueue
    )
    OutboundFacts(ctx).decision(lease, submission, at=ctx.clock.now())
    _log.info("runner-config gate: decision buffered", chunk_id=lease.chunk_id, node=lease.node_name)


def _apply_response(
    ctx: LoopContext,
    chunk_id: str,
    outcome: ApplyOutcome,
    next_envelope: NodeEnvelope | None,
    bindings: list[EnvBindingRecord],
) -> None:
    """Act on the apply-response: continue in place, hold at a hub node, or finish."""
    if outcome == ApplyOutcome.NEXT and next_envelope is not None:
        envs = environments_for(bindings)
        resume_from = ctx.sessions.resume_target(
            chunk_id,
            next_envelope.node,
            resolve_spawn_cwd(ctx.config.workspace_root, envs[0].workdir if envs else None),
        )
        Spawner(ctx).spawn(chunk_id, next_envelope, envs, via="apply-response", resume_from=resume_from)
    elif outcome == ApplyOutcome.HUB_NODE_TAKEN:
        _log.info("hub node took over — holding envs until terminal", chunk_id=chunk_id)
    elif outcome == ApplyOutcome.MIGRATED:
        # A cross-graph migration already released the route (#90) — tear the attempt down;
        # the chunk is claimed afresh under the new graph rather than continued in place.
        _log.info("chunk migrated to another graph — releasing envs", chunk_id=chunk_id)
        ctx.env_release.release_chunk(chunk_id)
    elif outcome == ApplyOutcome.DONE:
        ctx.env_release.release_chunk(chunk_id)
    elif outcome == ApplyOutcome.PARKED_AT_GATE:
        _log.info("chunk parked at human gate", chunk_id=chunk_id)  # waiting_on_human


def _advance_held_chunk(ctx: LoopContext, chunk_id: str) -> None:
    """Drive a chunk the runner holds with no active lease.

    Four shapes share this poll, all holding environments: a hub node polled toward its terminal
    outcome, a resolved gate, a chunk moved to a higher epoch, and an unknown chunk (blizzard#9).
    """
    try:
        detail = ctx.hub.get_chunk(chunk_id)
    except ChunkNotFoundError:
        _log.warning("hub reports held chunk unknown — releasing envs", chunk_id=chunk_id)
        ctx.env_release.release_chunk(chunk_id)
        return
    except HubClientError:
        return
    if detail.status == ChunkStatus.DONE:
        _log.info("delivery landed — releasing envs", chunk_id=chunk_id)
        ctx.env_release.release_chunk(chunk_id)
        return
    decision = detail.decision
    if decision is not None and decision.resolved_choice is not None and not decision.transitioned:
        _resolve_gate(ctx, chunk_id, decision)
        return
    hub_epoch = detail.latest_epoch
    if detail.status == ChunkStatus.RUNNING and hub_epoch is not None and hub_epoch > ctx.store.latest_epoch(chunk_id):
        # The strictly-higher epoch is load-bearing: a just-escalated chunk still derives
        # `running` at the SAME epoch until its fact flushes, and would re-spawn forever (#63).
        _spawn_into_held_node(ctx, chunk_id)
    elif detail.status == ChunkStatus.DELIVERING:
        # A chunk parked at a hub node — drive it one step; a no-op leaves this binding
        # held and polled again next tick (#65/#66).
        _poll_hub_node(ctx, chunk_id)
    # Every other shape keeps its binding and is polled again next tick.


def _poll_hub_node(ctx: LoopContext, chunk_id: str) -> None:
    """Drive a chunk parked at a hub node one step via ``POST /chunks/{id}/hub-advance``
    (#65/#66) — the re-drive path a hub node otherwise has no liveness poll for.

    A no-op upstream is expected and silent; a transport failure is likewise swallowed.
    """
    try:
        ctx.hub.hub_advance(chunk_id)
    except HubClientError:
        return  # hub unreachable — retried next tick


def _spawn_into_held_node(ctx: LoopContext, chunk_id: str) -> None:
    """Spawn the held chunk's current node into its already-bound, warm environment.

    The chunk advanced while this runner retained the route, so no active lease was minted
    for it and nothing else will spawn it (#63)."""
    bindings = ctx.store.bindings_for_chunk(chunk_id)
    if not bindings:
        _log.warning("held chunk advanced with no bound env — cannot spawn", chunk_id=chunk_id)
        return
    try:
        envelope = ctx.hub.get_envelope(chunk_id)
    except ChunkNotFoundError:
        _log.warning("hub reports advanced chunk unknown — releasing envs", chunk_id=chunk_id)
        ctx.env_release.release_chunk(chunk_id)
        return
    except HubClientError:
        return  # hub unreachable — the transition is durable at the hub; retry next tick
    _log.info("hub advanced held chunk into a fresh node — spawning", chunk_id=chunk_id)
    held = environments_for(bindings)
    resume_from = ctx.sessions.resume_target(
        chunk_id,
        envelope.node,
        resolve_spawn_cwd(ctx.config.workspace_root, held[0].workdir if held else None),
    )
    Spawner(ctx).spawn(chunk_id, envelope, held, via="advance", resume_from=resume_from)


def _resolve_gate(ctx: LoopContext, chunk_id: str, decision: DecisionView) -> None:
    """Record the resolving transition for a decided gate and continue in place.

    Reuses the parked step's epoch — no new lease was minted while parked — and references
    the decision id, which is what makes a transition out of a human-judged node legal."""
    submission = CompletionSubmission(
        choice=decision.resolved_choice or "",
        epoch=decision.epoch,
        runner_id=ctx.config.runner_id,
        from_node_id=decision.node_id,
        artifacts=[],  # the decision's artifacts already landed
        decision_id=decision.decision_id,
        # Not buffered, so stamped directly at submit (issue #84a).
        route_token=ctx.store.route_token(chunk_id),
    )
    try:
        response = ctx.hub.submit_completion(chunk_id, submission)
    except HubClientError:
        return  # the resolution is durable at the hub; retry next tick
    if response.outcome == ApplyOutcome.FAILURE:
        _log.warning("resolving transition rejected", chunk_id=chunk_id, detail=response.detail or "")
        return
    _log.info("gate resolved — advancing chunk", chunk_id=chunk_id, choice=decision.resolved_choice)
    _apply_response(ctx, chunk_id, response.outcome, response.next_envelope, ctx.store.bindings_for_chunk(chunk_id))


# Shared helpers


def _adopt_interrupted_claim(ctx: LoopContext, chunk_id: str) -> None:
    """Spawn the current node for a claimed chunk whose FILL crashed before the lease minted.

    The route is confirmed and the binding held, but no lease was ever minted, so recovery is a
    spawn of the current node from its idempotent envelope. Also the route-token recovery path:
    the adopted window spans the claim response, so a missing token re-keys here (issue #84b)."""
    bindings = ctx.store.bindings_for_chunk(chunk_id)
    if not bindings:
        _log.warning("adopt with no bound env — cannot spawn", chunk_id=chunk_id)
        return
    if ctx.store.route_token(chunk_id) is None:
        try:
            rekeyed = ctx.hub.rekey_route_token(chunk_id)
        except ChunkNotFoundError:
            _log.warning("hub reports adopted chunk unknown — releasing envs", chunk_id=chunk_id)
            ctx.env_release.release_chunk(chunk_id)
            return
        except HubClientError:
            return  # hub unreachable — the binding is durable; retry next tick
        ctx.store.set_route_token(chunk_id, token=rekeyed.route_token, at=ctx.clock.now())
    try:
        envelope = ctx.hub.get_envelope(chunk_id)
    except ChunkNotFoundError:
        _log.warning("hub reports adopted chunk unknown — releasing envs", chunk_id=chunk_id)
        ctx.env_release.release_chunk(chunk_id)
        return
    except HubClientError:
        return  # hub unreachable — the binding is durable; retry next tick
    _log.info("adopting interrupted claim — spawning current node", chunk_id=chunk_id)
    Spawner(ctx).spawn(chunk_id, envelope, environments_for(bindings), via="adopt")


def _resume_requeued_chunk(ctx: LoopContext, chunk_id: str) -> None:
    """Spawn a fresh attempt at the chunk's current node — its local hold is cleared (issue #53).

    The hold-clearing fact is already durable when this runs (``bzh:crash-correctness``). The
    retry budget is **carried, not reset** — an ordinary mint against the node's existing
    ``retries_max``, so a requeue buys exactly one more try."""
    bindings = ctx.store.bindings_for_chunk(chunk_id)
    if not bindings:
        _log.warning("requeue-resume with no bound env — cannot spawn", chunk_id=chunk_id)
        return
    try:
        envelope = ctx.hub.get_envelope(chunk_id)
    except ChunkNotFoundError:
        _log.warning("hub reports requeued chunk unknown — releasing envs", chunk_id=chunk_id)
        ctx.env_release.release_chunk(chunk_id)
        return
    except HubClientError:
        return  # hub unreachable — the requeue fact is durable; retry next tick
    _log.info("resuming requeued chunk — spawning current node", chunk_id=chunk_id)
    Spawner(ctx).spawn(chunk_id, envelope, environments_for(bindings), via="requeue-resume")


def _reclaim_interrupted(ctx: LoopContext, chunk_id: str, bindings: list[EnvBindingRecord]) -> None:
    """Complete a claim whose hub POST never landed — claim now, reusing the held binding.

    The environment was bound but the claim never landed, so the chunk still reads ``ready``.
    The route is claimed with the environment already held rather than re-acquired; a lost race
    releases the binding."""
    envs = environments_for(bindings)
    claim = RouteClaim(
        chunk_id=chunk_id,
        runner_id=ctx.config.runner_id,
        workspace_id=ctx.config.workspace_id,
        environment_ids=[b.environment_id for b in bindings],
    )
    try:
        outcome = ctx.hub.claim_route(claim)
    except HubClientError:
        return  # hub unreachable — the binding is durable; retry next tick
    if outcome.denied_paused is not None:
        # Refused outright because this runner is paused upstream, not lost to another
        # runner (issue #44).
        _log.info("interrupted claim denied — runner paused at the hub", chunk_id=chunk_id)
        ctx.env_release.release_chunk(chunk_id)
        return
    if outcome.conflict is not None or outcome.claimed is None:
        _log.info("interrupted claim lost the race — releasing binding", chunk_id=chunk_id)
        ctx.env_release.release_chunk(chunk_id)
        return
    _log.info("re-claimed interrupted chunk — spawning current node", chunk_id=chunk_id)
    # A reclaim is a fresh claim, so its token overwrites whatever this chunk's row held
    # before — a fresh claim always wins (issue #84a).
    ctx.store.set_route_token(chunk_id, token=outcome.claimed.route_token, at=ctx.clock.now())
    Spawner(ctx).spawn(chunk_id, outcome.claimed.envelope, envs, via="reclaim")


def _park_on_ask(ctx: LoopContext, lease: LeaseRecord, ask: AskRecord) -> None:
    """Park the chunk on a question: forward it to the hub and stop the reap clock.

    The local park fact stops the reap clock and keeps the lease from being re-parked or judged;
    env bindings stay held so the session is warm for the resume. No retry is consumed.
    """
    now = ctx.clock.now()
    ctx.usage.record_worker(lease, ctx.store.bindings_for_chunk(lease.chunk_id))
    OutboundFacts(ctx).question_asked(lease, ask, at=now)
    ctx.store.record_park(lease_id=lease.lease_id, chunk_id=lease.chunk_id, question_id=ask.question_id, parked_at=now)
    _log.info("chunk parked on question", chunk_id=lease.chunk_id, question_id=ask.question_id)


def _resume_if_answered(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Poll a parked lease's question; on an answer, resume the dormant session.

    Crash-safe and re-runnable: an unanswered question polls as a no-op and the reap clock stays
    stopped. Once answered the agent is reconstituted under the same session, lease and step.
    """
    if Spawner(ctx).suppressed(via="answer-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return
    park = ctx.store.open_park(lease.lease_id)
    if park is None:
        return  # not actually parked (raced with a resume)
    try:
        question = ctx.hub.get_question(park.question_id)
    except HubClientError:
        return  # hub unreachable — the park is durable; retry next tick
    if not question.answered or question.answer is None:
        return  # still waiting — reap clock stays stopped
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings:
        _log.warning("answered park with no bound env — cannot resume", chunk_id=lease.chunk_id)
        return

    # The human framing rides a leading `#` comment line and the answer itself is the
    # payload; the exact prose is unpinned.
    who = question.answered_by or "operator"
    message = f"# Answer from {who}. Continue.\n{question.answer}"
    pid = ctx.harness.resume_with_message(
        bindings[0].workdir,
        lease.session_id or "",
        message,
        stdout_path=Spawner(ctx).stdout_path(lease.lease_id),
        preamble=Spawner(ctx).preamble(lease, bindings),
        chunk_id=lease.chunk_id,
        # Reasserted, not sticky (issue #144) — see the judge call site's note.
        effort=lease.resolved_effort,
    )
    now = ctx.clock.now()
    # Same lease and session; record the new pid so the lease reads live again.
    ctx.store.record_spawn(
        lease.lease_id,
        pid=pid,
        process_start_time=ctx.process.start_time(pid) or "",
        session_id=lease.session_id or "",
        spawned_at=now,
    )
    ctx.store.record_park_resume(lease_id=lease.lease_id, question_id=park.question_id, resumed_at=now)
    OutboundFacts(ctx).answer_delivered(lease, park.question_id, at=now)
    _log.info("resumed dormant session with answer", chunk_id=lease.chunk_id, question_id=park.question_id, pid=pid)


def _resume_if_unpaused(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Poll a pause-parked lease's chunk; once the operator resumes it, restart its session (#46).

    Same lease, epoch and session; only ``pid``/``process_start_time`` are rewritten, so **no
    retry is consumed** — the pause cost the chunk a process, not an attempt. An **ask-parked**
    lease returns early even once unpaused, so a lift never conjures an absent answer."""
    if Spawner(ctx).suppressed(via="pause-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return
    try:
        detail = ctx.hub.get_chunk(lease.chunk_id)
    except ChunkNotFoundError:
        # The chunk is gone outright — not this step's abandon to make; the reconcile
        # sweep owns it and runs ahead of this step in the same tick.
        return
    except HubClientError:
        return  # hub unreachable — the park is durable; retry next tick
    if detail.pause is not None:
        return  # still paused — the reap clock stays stopped
    if detail.route is None or detail.route.runner_id != ctx.config.runner_id:
        return  # detached/reassigned while parked — PULL's sweep abandons it, not this step
    now = ctx.clock.now()
    if lease.lease_id in ctx.store.ask_parked_lease_ids():
        # Dormant on a question underneath the pause: clearing the pause-park is the whole
        # action, and an answer — not this resume — restarts it.
        ctx.store.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
        _log.info("pause lifted on an ask-parked chunk — awaiting its answer", chunk_id=lease.chunk_id)
        return
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings or lease.session_id is None:
        _log.warning("unpaused chunk has no warm env/session — cannot resume", chunk_id=lease.chunk_id)
        return
    # The un-armable spawn-record gap: no crash point can arm a window whose recovery input
    # (the new pid) does not yet exist.
    pid = ctx.harness.resume_with_message(
        bindings[0].workdir,
        lease.session_id,
        _PAUSE_RESUME_MESSAGE,
        stdout_path=Spawner(ctx).stdout_path(lease.lease_id),
        preamble=Spawner(ctx).preamble(lease, bindings),
        chunk_id=lease.chunk_id,
        # Reasserted, not sticky (issue #144) — see the judge call site's note.
        effort=lease.resolved_effort,
    )
    ctx.store.record_spawn(
        lease.lease_id,
        pid=pid,
        process_start_time=ctx.process.start_time(pid) or "",
        session_id=lease.session_id,  # unchanged — same session under the same lease
        spawned_at=now,
    )
    ctx.store.record_pause_park_resume(lease_id=lease.lease_id, resumed_at=now)
    _log.info(
        "resumed dormant session after an operator unpause",
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        epoch=lease.epoch,
        pid=pid,
    )


# EXTERNAL SUBSCRIPTION USAGE SAMPLE (issue #218)


def _external_usage_payload(snapshot: ExternalSubscriptionUsageSnapshot) -> dict[str, object]:
    """The stable JSON shape for a sampled snapshot — both this attempt's stored
    ``payload`` and its buffered outbound report use this exact shape, and phase 3's wire
    fact payload is defined to match it field-for-field: ``sampled_at``, ``windows``, and
    per-window ``window``/``utilization_pct``/``resets_at``/``window_seconds``."""
    return {
        "sampled_at": iso_utc(snapshot.sampled_at),
        "windows": [
            {
                "window": w.window,
                "utilization_pct": w.utilization_pct,
                "resets_at": iso_utc(w.resets_at),
                "window_seconds": w.window_seconds,
            }
            for w in snapshot.windows
        ],
    }


def sample_external_subscription_usage(ctx: LoopContext) -> None:
    """Sample the harness's own subscription rate-limit utilization (issue #218).

    The cadence anchor is derived as ``max(sampled_at)``, never a stored "last sampled" column,
    and an attempt row is recorded either way — a ``NULL`` payload when nothing was produced.
    """
    try:
        anchor = ctx.store.last_external_usage_attempt_at()
        if anchor is not None:
            elapsed = ctx.clock.now() - anchor
            if elapsed < timedelta(seconds=ctx.config.external_usage_sample_interval_seconds):
                return
        snapshot = ctx.harness.sample_external_subscription_usage()
        if snapshot is None:
            ctx.store.record_external_usage_attempt(
                sampled_at=ctx.clock.now(), payload=None, report_kind="", report_payload=""
            )
            return
        payload = json.dumps(_external_usage_payload(snapshot))
        ctx.store.record_external_usage_attempt(
            sampled_at=ctx.clock.now(),
            payload=payload,
            report_kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
            report_payload=payload,
        )
    except Exception as exc:  # second line of defense — the adapter contract already promises this
        _log.warning("external subscription usage sample step failed", detail=str(exc))
