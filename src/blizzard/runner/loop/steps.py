"""The reconciliation step functions — REAP → PULL → FILL → ADVANCE (``bzh:steppable-loop``).

Each is an individually callable function of a :class:`LoopContext`. Every step is
idempotent and holds no state of its own — all facts live in the runner store, so a crash
mid-tick and a restart re-run the tick harmlessly; startup recovery is REAP running first.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import datetime, timedelta

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import LEASE_PREFIX, mint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import SessionMode
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.domain.lease_auth import mint_lease_token
from blizzard.runner.domain.leases import as_utc, is_heartbeat_stale
from blizzard.runner.domain.takeover import wrapped_takeover_command
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    WorkspaceAcquisitionError,
)
from blizzard.runner.harness.adapter import HarnessSpawnError, WorkerPreamble
from blizzard.runner.harness.external_usage import ExternalSubscriptionUsageSnapshot
from blizzard.runner.harness.preamble import render_worker_preamble
from blizzard.runner.harness.spawn_cwd import resolve_spawn_cwd
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.loop.checks import DEFAULT_CHECK_TIMEOUT
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError
from blizzard.runner.loop.internal.subprocess_worktree_git import WorktreeGitError
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.store.repository import (
    AskRecord,
    BufferedFact,
    CheckResultRecord,
    EnvBindingRecord,
    GitCommitDeclarationRecord,
    IWriteRunnerStore,
    LeaseRecord,
    NewLease,
    PoolHead,
)
from blizzard.wire.completion import (
    CheckResult,
    CompletionSubmission,
    SubmittedArtifact,
    checks_gate_violated,
    produces_coverage,
)
from blizzard.wire.decision import DecisionSubmission, DecisionView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeConfig, NodeEnvelope
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    EVENT_RECORDED,
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
    LEASE_MINTED,
    QUESTION_ASKED,
    RUNNER_LOCALLY_PAUSED,
    RunnerFact,
    RunnerFactBatch,
)
from blizzard.wire.graph import ProducesEntry
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

# Closure reasons (lease_closures.reason).
_TRANSITIONED = "transitioned"
_REAPED = "reaped"
_FAILED = "failed"
_ESCALATED = "escalated"
_PARKED = "parked"  # a runner-config gate: the node-step completed, the chunk parks on a decision
_RELEASED = "released"  # the chunk was found reassigned/detached/unknown — abandon, no requeue (blizzard#9)

#: The message RESUME delivers into a marked session on a restart — ``#``-prefixed so it
#: is inert in prose and in a behavior script alike. The exact prose is unpinned.
_RESTART_RESUME_MESSAGE = "# The supervisor restarted; continue your task where you left off."

#: The message ADVANCE delivers into a session the operator paused and resumed (issue #46).
#: Same inert ``#``-prefixed framing; the exact prose is unpinned.
_PAUSE_RESUME_MESSAGE = "# The operator resumed this chunk; continue your task where you left off."

# The two outbound-buffer fact kinds the flusher handles specially (every other kind
# flushes generically to POST /events).
_COMPLETION_KIND = "completion.submitted"
_DECISION_KIND = "decision.submitted"

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

# ABANDON — the reassigned/detached release. A crash here leaves a still-active lease with
# a dead pid, envs unreleased and no closure; recovery is `_reconcile_leases`.
_CP_ABANDON_AFTER_KILL = crashpoint(
    "abandon.after-kill.before-release", "detached worker killed; environments not yet released"
)

# PAUSE — the operator's per-chunk pause park (issue #46): the worker dies, the claim, route,
# epoch and envs survive. A crash before the park is recovered by `_resume_marked_lease`.
_CP_PAUSE_PARK_AFTER_KILL = crashpoint(
    "pause.after-kill.before-park", "paused worker killed; pause-park not yet durable"
)

# PULL — the single outbound flusher (store-and-forward drain).
_CP_PULL_BEFORE = crashpoint("pull.before-flush", "entered PULL; registry synced, buffer not drained")
_CP_PULL_AFTER = crashpoint("pull.after-flush", "PULL done; buffer drained as far as it could")

# FILL — peek -> acquire -> BIND -> claim -> spawn. The local binding is written *before*
# the hub claim, so a crash in that window is reconciled next tick — never a strand.
_CP_FILL_BEFORE_ACQUIRE = crashpoint("fill.before-env-acquire", "peeked a ready chunk; envs not acquired")
_CP_FILL_AFTER_ACQUIRE = crashpoint("fill.after-env-acquire.before-bind", "envs acquired; binding not recorded")
_CP_FILL_AFTER_BIND = crashpoint("fill.after-bind.before-claim", "binding recorded; route not claimed at the hub")
_CP_FILL_AFTER_CLAIM = crashpoint("fill.after-claim.before-spawn", "hub holds the route; lease not minted")

# SPAWN (shared by FILL's first spawn, ADVANCE's continue-in-place, and requeue): the
# lease-mint -> spawn -> record window is the orphan-lease window REAP must absorb.
_CP_SPAWN_AFTER_MINT = crashpoint("spawn.after-lease-mint.before-spawn", "lease minted; worker not spawned")
_CP_SPAWN_AFTER_SPAWN = crashpoint("spawn.after-spawn", "worker spawned; pid recorded")

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


def _stdout_path(ctx: LoopContext, lease_id: str, generation: int) -> str:
    """This lease's per-generation stdout redirect target, or ``""`` for no redirect.

    Scoped to ``(lease_id, generation)`` so a readback sees only that attempt's own line,
    and opened in append mode so a retry reusing the generation number does not collide."""
    if not ctx.config.worker_stdout_dir:
        return ""
    return os.path.join(ctx.config.worker_stdout_dir, f"{lease_id}.{generation}.stdout")


def _stderr_path(ctx: LoopContext, lease_id: str, generation: int) -> str:
    """This lease's per-generation harness-**stderr** redirect target (issue #125, change
    L(iii)), or ``""`` for no redirect — the sibling of :func:`_stdout_path`, so a launched
    worker that crashed to stderr leaves a readable tail for the ``worker-lost`` event."""
    if not ctx.config.worker_stdout_dir:
        return ""
    return os.path.join(ctx.config.worker_stdout_dir, f"{lease_id}.{generation}.stderr")


def _stderr_tail(ctx: LoopContext, lease: LeaseRecord, *, limit: int = 2000) -> str:
    """The tail of this lease's most-recent captured spawn-stderr (change L(iii)), or ``""``.

    Best-effort and never raises (a hung-but-live worker that never crashed to stderr, or a
    test with no ``worker_stdout_dir``, is the ordinary empty case) — folded into a
    `_fail_attempt` event's detail so a dead worker's last words reach the operator."""
    generation = ctx.store.lease_generation(lease.lease_id)
    if generation <= 0:
        return ""
    text = _read_stdout(_stderr_path(ctx, lease.lease_id, generation))
    return text[-limit:] if text else ""


def _pending_generation(ctx: LoopContext, lease_id: str) -> int:
    """The spawn generation this lease's next spawn/resume is about to mint — one past
    :meth:`~blizzard.runner.store.repository.IReadRunnerStore.lease_generation`'s
    durably-recorded count, read *before* this attempt's own ``record_spawn`` call
    lands. Every write call site (:func:`_spawn_attempt`, and each resume family
    member) reads this to name its own :func:`_stdout_path` ahead of that call."""
    return ctx.store.lease_generation(lease_id) + 1


def _read_stdout(path: str) -> str:
    """The per-lease stdout file's full text, or ``""`` when absent/unreadable.

    Never raises: a missing file (nothing was ever redirected, or it was already
    cleaned up at release) is the ordinary "no envelope" case the caller falls back
    from, not a fault to log."""
    if not path:
        return ""
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _record_worker_usage(ctx: LoopContext, lease: LeaseRecord, bindings: list[EnvBindingRecord]) -> None:
    """Record just this attempt's spawn/resume invocation usage — no judgement ran.

    Keyed on ``(lease, generation, kind)``, so it is idempotent across a re-run and
    distinct from the next generation's own resume fact (issue #58).
    """
    generation = ctx.store.lease_generation(lease.lease_id)
    kind: UsageKind = "spawn" if generation <= 1 else "resume"
    worker_sample = _worker_usage_sample(ctx, lease, bindings, generation=generation, kind=kind)
    if worker_sample is not None:
        _store_usage(ctx, lease, generation=generation, sample=worker_sample)


def _record_attempt_usage(
    ctx: LoopContext, lease: LeaseRecord, bindings: list[EnvBindingRecord], *, judge_output: str
) -> None:
    """Record this attempt's harness usage: the spawn/resume invocation whose exit ADVANCE
    is judging, and the judgement resume that elicited its verdict — each its own fact,
    keyed on ``(lease, generation, kind)``, so a crash finds both durable or neither."""
    _record_worker_usage(ctx, lease, bindings)
    generation = ctx.store.lease_generation(lease.lease_id)
    # Attribute to the lease's own `resolved_model` stamp (issue #144), not the adapter
    # default: a judge turn on a sonnet session would otherwise book its spend against opus.
    judge_sample = ctx.harness.parse_usage(judge_output, "judge", model=lease.resolved_model)
    if judge_sample is not None:
        _store_usage(ctx, lease, generation=generation, sample=judge_sample)


def _store_usage(ctx: LoopContext, lease: LeaseRecord, *, generation: int, sample: UsageSample) -> None:
    ctx.store.record_usage(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        node_id=lease.node_id,
        epoch=lease.epoch,
        generation=generation,
        sample=sample,
        recorded_at=ctx.clock.now(),
    )


def _worker_usage_sample(
    ctx: LoopContext, lease: LeaseRecord, bindings: list[EnvBindingRecord], *, generation: int, kind: UsageKind
) -> UsageSample | None:
    """This attempt's own spawn/resume usage, parsed off *this generation's own* stdout
    envelope, falling back to a transcript-summed, cost-absent sample when none survived.
    Never fabricated: no envelope and no transcript is simply no fact."""
    output = _read_stdout(_stdout_path(ctx, lease.lease_id, generation))
    # Same attribution fallback as the judge fact (issue #144): on a resume the stamp is
    # what the session was MINTED with, not what a fresh resolution would produce now.
    sample = ctx.harness.parse_usage(output, kind, model=lease.resolved_model) if output else None
    if sample is not None:
        return sample
    if lease.session_id is None:
        return None
    if ctx.transcripts is None:
        return None
    fallback_workdir = bindings[0].workdir if bindings else None
    spawn_cwd = resolve_spawn_cwd(ctx.config.workspace_root, fallback_workdir)
    lines = ctx.transcripts.read_raw_lines(lease.session_id, spawn_cwd=spawn_cwd)
    if not lines:
        return None
    return ctx.harness.sum_transcript_usage(lines, kind, model=lease.resolved_model)


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
            _fail_attempt(ctx, lease, reason=_REAPED, via="reap")
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
            _fail_attempt(ctx, lease, reason=_REAPED, via="reap")
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
        _abandon_reassigned(ctx, lease, via="resume")
        return
    except HubClientError:
        # Hub unreachable — the intent is durable and the envs stay held. Resuming blind
        # would risk re-asserting authority over a chunk that may have been reassigned.
        return
    ours = detail.route is not None and detail.route.runner_id == ctx.config.runner_id
    if ours and detail.pause is not None:
        _kill_and_park_paused(ctx, lease, via="resume")
    elif detail.status == ChunkStatus.RUNNING and ours:
        _resume_in_place(ctx, lease)
    else:
        _abandon_reassigned(ctx, lease, via="resume")


def _resume_preamble(ctx: LoopContext, lease: LeaseRecord, bindings: list[EnvBindingRecord]) -> WorkerPreamble:
    """The per-lease identity a resumed worker needs to reach the runner for its lease.

    A resume inherits none of the spawn env, so the identity is re-supplied. Only the token's
    hash is ever persisted, so the token itself is **re-minted** here, invalidating the prior one.
    """
    lease_token, token_hash = mint_lease_token()
    ctx.store.record_lease_token(lease.lease_id, token_hash, ctx.clock.now())
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in bindings],
        lease_id=lease.lease_id,
        local_api_url=ctx.config.local_api_url,
        lease_token=lease_token,
    )


def _resume_in_place(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Kill any survivor, then resume the session under the same lease/epoch/session.

    Kill-first is what prevents two processes on one session — the epoch is not. Only
    ``pid``/``process_start_time`` are rewritten, so no retry is consumed. The brake is checked
    **before the kill**: gating after would kill the survivor and leave it un-re-attached."""
    if _spawn_suppressed(ctx, via="resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
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
        _abandon_reassigned(ctx, lease, killed=True, via="resume")
        return
    # The resume-with-message -> record_spawn gap is the un-armable spawn-record window: no
    # crash point can arm a window whose recovery input (the new pid) does not yet exist.
    pid = ctx.harness.resume_with_message(
        bindings[0].workdir,
        lease.session_id,
        _RESTART_RESUME_MESSAGE,
        stdout_path=_stdout_path(ctx, lease.lease_id, _pending_generation(ctx, lease.lease_id)),
        preamble=_resume_preamble(ctx, lease, bindings),
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


def _abandon_reassigned(ctx: LoopContext, lease: LeaseRecord, *, killed: bool = False, via: str) -> None:
    """Release a chunk the hub reassigned, detached, or no longer knows about (blizzard#9) —
    reached from restart-resume or a live tick.

    No epoch bump and no requeue — the work is not this runner's any more. The lease closes
    ``released``, and any open ask park is retired alongside (blizzard#202)."""
    now = ctx.clock.now()
    if lease.pid is not None and not killed:
        ctx.process.kill(lease.pid)
    _CP_ABANDON_AFTER_KILL.reached()  # worker killed; envs not yet released — recovery is the next tick's re-scan
    _release_all(ctx, lease.chunk_id)
    park = ctx.store.open_park(lease.lease_id)
    if park is not None:
        ctx.store.record_park_resume(lease_id=lease.lease_id, question_id=park.question_id, resumed_at=now)
    ctx.store.record_closure(
        lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=_RELEASED, closed_at=now
    )
    ctx.store.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
    _log.info("abandoned reassigned/detached/unknown chunk", chunk_id=lease.chunk_id, lease_id=lease.lease_id, via=via)


def _kill_and_park_paused(ctx: LoopContext, lease: LeaseRecord, *, via: str) -> None:
    """Kill a paused chunk's worker and park its lease — the claim is **kept** (issue #46).

    The deliberate inverse of :func:`_abandon_reassigned`: no environment released, no closure,
    no epoch bump, no lease minted — **no retry is consumed**, and the route, epoch and session
    all survive. Not gated by the local brake: a kill is not a spawn."""
    now = ctx.clock.now()
    if lease.pid is not None:
        ctx.process.kill(lease.pid)
    _CP_PAUSE_PARK_AFTER_KILL.reached()  # worker dead; the park is not yet durable
    ctx.store.record_pause_park(lease_id=lease.lease_id, chunk_id=lease.chunk_id, parked_at=now)
    ctx.store.record_resume_clear(lease_id=lease.lease_id, cleared_at=now)
    _log.info(
        "parked chunk on an operator pause — claim retained",
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        epoch=lease.epoch,
        via=via,
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
            _abandon_reassigned(ctx, lease, via="pull")
            continue
        except HubClientError:
            continue  # hub unreachable — last-known directive holds; keep working
        if detail.status == ChunkStatus.STOPPED:
            # Honor the terminal fact directly (issue #118), rather than waiting on the
            # route check below to observe the release.
            _abandon_reassigned(ctx, lease, via="pull")
        elif detail.route is None or detail.route.runner_id != ctx.config.runner_id:
            _abandon_reassigned(ctx, lease, via="pull")
        elif detail.pause is not None and lease.lease_id not in pause_parked:
            _kill_and_park_paused(ctx, lease, via="pull")


def _reassigned_or_detached(ctx: LoopContext, lease: LeaseRecord) -> bool:
    """True iff the hub no longer routes ``lease``'s chunk to this runner, or the
    chunk is gone outright (blizzard#9).

    Unreachable hub → ``False``: a transport failure is never read as a detach. A 404 is the
    one exception — terminal, not something to wait out (blizzard#9)."""
    try:
        detail = ctx.hub.get_chunk(lease.chunk_id)
    except ChunkNotFoundError:
        return True  # the chunk no longer exists at the hub — terminal, not retryable
    except HubClientError:
        return False  # hub unreachable — last-known directive holds; keep working
    return detail.route is None or detail.route.runner_id != ctx.config.runner_id


def flush_outbound(ctx: LoopContext) -> None:
    """Drain the outbound buffer in FIFO order until a fact fails to deliver."""
    for fact in ctx.store.pending_outbound():
        if not _flush_one(ctx, fact):
            break  # transport failure — stop; strict FIFO, retry the backlog next tick


def _flush_one(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Deliver one buffered fact. Return False on a transport failure (stop the drain)."""
    if fact.kind == _COMPLETION_KIND:
        return _flush_completion(ctx, fact)
    if fact.kind == _DECISION_KIND:
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
        _fail_attempt(ctx, lease, reason=_FAILED, via="pull")
        return True
    ctx.store.record_closure(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        node_id=lease.node_id,
        reason=_PARKED,
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
        _fail_attempt(ctx, lease, reason=_FAILED, via="pull")
        return
    now = ctx.clock.now()
    ctx.store.record_closure(
        lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=_TRANSITIONED, closed_at=now
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
    _escalate(ctx, lease, reason=f"spend cap ${cap:.2f} reached (spend ${cost.cost_usd:.2f}{partial_note})")
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
            _release_all(ctx, chunk_id)
            continue
        except HubClientError:
            continue  # hub unreachable — the binding is durable; retry next tick
        if chunk_id in requeue_pending:
            # An explicit human decision (issue #53) outranks every other branch below —
            # nothing here should second-guess it.
            ours = detail.route is not None and detail.route.runner_id == ctx.config.runner_id
            if not ours:
                _log.info("releasing binding — chunk requeued locally but no longer routed here", chunk_id=chunk_id)
                _release_all(ctx, chunk_id)
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
            _release_all(ctx, chunk_id)
        elif detail.route is None:
            # No live route, and neither claimable nor ours to adopt (blizzard#202). Release
            # explicitly instead of matching no branch and leaking the binding forever.
            _log.info(
                "releasing binding — hub reports no live route in a non-ready, non-running state",
                chunk_id=chunk_id,
                hub_status=str(detail.status),
            )
            _release_all(ctx, chunk_id)


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
        _emit_command_failed(
            ctx,
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
        _release_binding(ctx, entry.chunk_id, acquired)
        return False
    if outcome.denied_terminal is not None:
        # The chunk reached a terminal state between this peek and this claim (issue #118)
        # — not a race loss. Undo the binding and move on; it cannot be peeked again.
        _log.info(
            "route claim denied — chunk is terminal",
            chunk_id=entry.chunk_id,
            status=outcome.denied_terminal.status,
        )
        _release_binding(ctx, entry.chunk_id, acquired)
        return True  # peek fresh next iteration
    if outcome.conflict is not None or outcome.claimed is None:
        _log.info("route claim lost the race", chunk_id=entry.chunk_id)
        _release_binding(ctx, entry.chunk_id, acquired)  # someone else won — undo our binding
        return True  # peek fresh next iteration

    _CP_FILL_AFTER_CLAIM.reached()
    # Stash the won claim's plaintext route token (issue #84a) before spawning: every later
    # reader takes it out of the store, never off `outcome.claimed` directly.
    ctx.store.set_route_token(entry.chunk_id, token=outcome.claimed.route_token, at=ctx.clock.now())
    resume_from = _resolve_session(
        ctx,
        entry.chunk_id,
        outcome.claimed.envelope.node,
        resolve_spawn_cwd(ctx.config.workspace_root, acquired[0].workdir if acquired else None),
    )
    _spawn_attempt(ctx, entry.chunk_id, outcome.claimed.envelope, acquired, via="fill", resume_from=resume_from)
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

    # 1. Confirm the declared git commits read-only against the forge (issue #143). A failed
    #    verify is surfaced informationally, never re-raised into a crash-looping tick.
    artifacts, declared_this_attempt = _verify_and_collect_git_commits(ctx, lease, bindings)

    # 1b. This operator gates this node by name, so the outcome is a human's: buffer a
    #      Decision instead of eliciting a verdict. Not a spawn, so it is ungated.
    if lease.node_name in ctx.config.gates:
        _buffer_decision(ctx, lease, artifacts)
        return

    # 2. Elicit the verdict via the judgement resume — a spawn primitive, gated here rather
    #    than at the top so the non-spawn work above still happens while paused (issue #45).
    if _spawn_suppressed(ctx, via="advance", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
        return

    # 1c. Run the node's `checks:` before the judgement (issue #114), against the tree the
    #      worker just left — the same tree its judgement and the gate are rendered on.
    check_records = _run_or_read_checks(ctx, lease, envelope, bindings)

    # The check results ride between the authored judgement prose and the `<Choice>` tail,
    # so the worker judges against mechanical truth (issue #114).
    prompt = (envelope.judgement_prompt or "") + _checks_block(check_records) + _elicitation_tail(envelope)
    # The judgement turn carries a re-minted lease identity; the worker is already dead,
    # so invalidating its token orphans nothing.
    output = ctx.harness.judge(
        bindings[0].workdir,
        lease.session_id,
        prompt,
        preamble=_resume_preamble(ctx, lease, bindings),
        chunk_id=lease.chunk_id,
        # Reassert the stamped effort (issue #144): effort is NOT session-sticky, so a
        # resume that omits it drops the declared value back to the ambient default.
        effort=lease.resolved_effort,
        model=lease.resolved_model,
    )

    # 2c. Record this attempt's harness usage (issue #58) *before* the verdict is parsed, so
    #      a verdict-less fail does not discard the spend the attempt genuinely burned.
    _record_attempt_usage(ctx, lease, bindings, judge_output=output)

    choice = ctx.harness.parse_verdict(output)
    if choice is None:
        _log.warning("verdict-less judgement — failing attempt", chunk_id=lease.chunk_id, lease_id=lease.lease_id)
        _fail_attempt(ctx, lease, reason=_FAILED, via="advance")
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
        _fail_attempt(ctx, lease, reason=_FAILED, via="advance")
        return

    # 2a. Nudge-once (issue #113): the guard fact is recorded BEFORE the resume, which is what
    #      makes "at most one nudge per (lease, epoch)" hold across a kill -9 at either point.
    assessment = ctx.harness.parse_assessment(output)
    attachments = ctx.store.attachments_for_lease(lease.lease_id)
    missing = _missing_produces(envelope, artifacts, attachments)
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
            _nudge_message(missing),
            preamble=_resume_preamble(ctx, lease, bindings),
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
            _store_usage(ctx, lease, generation=nudge_generation, sample=nudge_sample)
        # Re-read: a worker that attached during the nudge must have its content picked
        # up before assembly below, not the assessment fallback it just corrected.
        attachments = ctx.store.attachments_for_lease(lease.lease_id)
        # Re-verify: overlaid by repo name rather than appended, so a hiccup cannot regress
        # an artifact this attempt already has while a genuine amendment still wins.
        post_nudge_artifacts, _ = _verify_and_collect_git_commits(
            ctx, lease, bindings, already_declared=declared_this_attempt
        )
        by_repo = {a.name: a for a in artifacts}
        by_repo.update({a.name: a for a in post_nudge_artifacts})
        artifacts = list(by_repo.values())

    # 2b. Harvest asset artifacts for any `produces` name no git commit covers, read from
    #      the durable store so a restart between attach and completion still sees it.
    artifacts += _collect_asset_artifacts(envelope, artifacts, assessment, attachments)

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
    payload = json.dumps({"submission": submission.model_dump(mode="json")})
    ctx.store.enqueue_outbound(
        kind=_COMPLETION_KIND,
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        payload=payload,
        created_at=ctx.clock.now(),
    )
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
    payload = json.dumps({"submission": submission.model_dump(mode="json")})
    ctx.store.enqueue_outbound(
        kind=_DECISION_KIND,
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        payload=payload,
        created_at=ctx.clock.now(),
    )
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
        envs = _bindings_as_environments(bindings)
        resume_from = _resolve_session(
            ctx,
            chunk_id,
            next_envelope.node,
            resolve_spawn_cwd(ctx.config.workspace_root, envs[0].workdir if envs else None),
        )
        _spawn_attempt(ctx, chunk_id, next_envelope, envs, via="apply-response", resume_from=resume_from)
    elif outcome == ApplyOutcome.HUB_NODE_TAKEN:
        _log.info("hub node took over — holding envs until terminal", chunk_id=chunk_id)
    elif outcome == ApplyOutcome.MIGRATED:
        # A cross-graph migration already released the route (#90) — tear the attempt down;
        # the chunk is claimed afresh under the new graph rather than continued in place.
        _log.info("chunk migrated to another graph — releasing envs", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
    elif outcome == ApplyOutcome.DONE:
        _release_all(ctx, chunk_id)
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
        _release_all(ctx, chunk_id)
        return
    except HubClientError:
        return
    if detail.status == ChunkStatus.DONE:
        _log.info("delivery landed — releasing envs", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
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
        _release_all(ctx, chunk_id)
        return
    except HubClientError:
        return  # hub unreachable — the transition is durable at the hub; retry next tick
    _log.info("hub advanced held chunk into a fresh node — spawning", chunk_id=chunk_id)
    held = _bindings_as_environments(bindings)
    resume_from = _resolve_session(
        ctx,
        chunk_id,
        envelope.node,
        resolve_spawn_cwd(ctx.config.workspace_root, held[0].workdir if held else None),
    )
    _spawn_attempt(ctx, chunk_id, envelope, held, via="advance", resume_from=resume_from)


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


def _spawn_suppressed(ctx: LoopContext, *, via: str, chunk_id: str, lease_id: str | None = None) -> bool:
    """True — and logged once — when the runner's own brake blocks this spawn (issue #45).

    Reads ``local_paused`` only, and is called before every spawn primitive's first mutation, so
    a suppressed spawn writes no fact, kills no pid and mints no lease. Which primitives it must
    cover is held mechanically by ``tests/test_spawn_suppressed_registry.py``."""
    if not ctx.store.local_paused(ctx.config.runner_id):
        return False
    _log.info(
        "spawn suppressed — locally paused",
        runner_id=ctx.config.runner_id,
        via=via,
        chunk_id=chunk_id,
        lease_id=lease_id,
    )
    return True


def _resolve_session(ctx: LoopContext, chunk_id: str, node: NodeConfig, spawn_cwd: str | None) -> str | None:
    """The prior session id a node-entry spawn resumes, or ``None`` to mint fresh (#115, #144).

    **Only the resume-vs-mint decision** — the configuration a spawn runs under resolves
    elsewhere. No match anywhere falls back to fresh: a resume target is best-effort.
    """
    if node.session is SessionMode.FRESH:
        return None
    if node.session_name is not None:
        return _resume_pool_head(ctx, chunk_id, node, spawn_cwd)
    return ctx.store.latest_session_id(chunk_id, node.session_source)


def _resume_pool_head(ctx: LoopContext, chunk_id: str, node: NodeConfig, spawn_cwd: str | None) -> str | None:
    """The named pool's head if it is still resumable, else ``None`` to mint a new one."""
    pool = node.session_name or ""
    head = ctx.store.pool_head(chunk_id, pool)
    if head is None:
        return None  # an empty pool — this member mints the head
    breach = _rotation_breach(ctx, head, node, spawn_cwd)
    if breach is None:
        return head.session_id
    _log.info(
        "rotating session pool",
        chunk_id=chunk_id,
        session_pool=pool,
        breached=breach,
        old_session_id=head.session_id,
    )
    return None


def _rotation_breach(ctx: LoopContext, head: PoolHead, node: NodeConfig, spawn_cwd: str | None) -> str | None:
    """Why this pool head must not be resumed, or ``None`` when it may be (issue #144).

    A head is resumed only while every *readable* threshold is under bound and its stamped model
    still matches the resolved one. An unreadable signal is *not measured* and never a breach.
    """
    # Model drift first: the one check that needs no telemetry, and an edited declaration
    # should rotate regardless of how much context the old head accumulated.
    resolved = ctx.harness.resolve_model(node.session_model) if node.session_model else None
    if resolved is not None and head.resolved_model is not None and head.resolved_model != resolved:
        return "model-drift"

    rotate = node.session_rotate
    if rotate is None:
        return None  # the declaration bounds nothing

    if rotate.max_context_tokens is not None:
        tokens = ctx.store.session_context_tokens(head.session_id)
        if tokens is not None and tokens > rotate.max_context_tokens:
            return "max_context_tokens"

    # A count is never an unknown — it is the number of rows that exist.
    if (
        rotate.max_invocations is not None
        and ctx.store.session_invocation_count(head.session_id) > rotate.max_invocations
    ):
        return "max_invocations"

    if rotate.max_transcript_bytes is not None and ctx.transcripts is not None:
        # `size_bytes` returns `None` for an unreadable transcript — treated as unknown,
        # never a zero that would make the threshold silently inert.
        size = ctx.transcripts.size_bytes(head.session_id, spawn_cwd=spawn_cwd)
        if size is not None and size > rotate.max_transcript_bytes:
            return "max_transcript_bytes"

    return None


def _resolve_model_and_effort(
    ctx: LoopContext, chunk_id: str, node: NodeConfig, resume_from: str | None
) -> tuple[str | None, str | None]:
    """The model and effort this spawn runs under, and stamps (issue #144).

    **The stamp describes the session, not the preference.** A spawn that *resumes* inherits both
    from the resumed session's own stamp, and an inherited ``None`` stays *unknown*.
    """
    if resume_from is not None:
        prior = ctx.store.lease_for_session(resume_from)
        return (prior.resolved_model, prior.resolved_effort) if prior is not None else (None, None)
    model = ctx.harness.resolve_model(node.session_model)
    return (model, ctx.harness.resolve_effort(node.session_effort))


def _spawn_attempt(
    ctx: LoopContext,
    chunk_id: str,
    envelope: NodeEnvelope,
    environments: list[AcquiredEnvironment],
    *,
    via: str,
    resume_from: str | None = None,
) -> None:
    """Mint a fresh-epoch lease and spawn a headless worker for a node-step.

    Always its caller's final statement, with no post-spawn logic after it — that is what lets
    the local-pause gate stay a silent ``None`` return no caller can misread as "spawn failed"
    (issue #45). The sole funnel into ``ctx.harness.spawn``, so a re-spawn joins its pool."""
    if _spawn_suppressed(ctx, via=via, chunk_id=chunk_id):
        return
    now = ctx.clock.now()
    # Mint above the max of both floors (bzh:epoch-fencing, #112): the local fence alone is 0
    # for a chunk this runner never drove, so a migrated chunk would mint below hub truth.
    epoch = max(ctx.store.latest_epoch(chunk_id), envelope.epoch) + 1
    lease_id = mint(LEASE_PREFIX, ctx.clock)
    node = envelope.node
    retries_max = node.retries_max if node.retries_max is not None else ctx.config.default_retries_max
    resolved_model, resolved_effort = _resolve_model_and_effort(ctx, chunk_id, node, resume_from)
    ctx.store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id=envelope.graph_id,
            node_id=node.node_id,
            node_name=node.node_name,
            epoch=epoch,
            runner_id=ctx.config.runner_id,
            retries_max=retries_max,
            session_name=node.session_name,
            resolved_model=resolved_model,
            resolved_effort=resolved_effort,
            created_at=now,
        )
    )
    # A per-lease capability token (issue #113): only its hash is stashed durably, the
    # plaintext carried forward to the spawn preamble alone and never persisted.
    lease_token, token_hash = mint_lease_token()
    ctx.store.record_lease_token(lease_id, token_hash, now)
    # The lease is a hub-bound fact and the fence input the completion check consumes, so it
    # is buffered ahead of any completion minted under it (FIFO), stamped with the route token.
    ctx.store.enqueue_outbound(
        kind=LEASE_MINTED,
        chunk_id=chunk_id,
        lease_id=lease_id,
        payload=json.dumps({"chunk_id": chunk_id, "epoch": epoch, "route_token": ctx.store.route_token(chunk_id)}),
        created_at=now,
    )
    _CP_SPAWN_AFTER_MINT.reached()  # lease minted, worker not spawned — the orphan-lease window REAP absorbs
    # The effective workspace prompt is the store's runtime override when set, else the static
    # config prompt — read here so a replace applies to the next spawn with no restart.
    override = ctx.store.workspace_prompt_override(ctx.config.workspace_id)
    workspace_prompt = override if override is not None else ctx.config.workspace_prompt
    # Read ONLY when this spawn resumes a session (issue #149), so a fresh one can never
    # elide prose it has never seen; nothing recorded reads `None` and renders in full.
    prior_preamble = ctx.store.session_preamble_fingerprint(resume_from) if resume_from else None
    rendered = render_worker_preamble(
        runner_prompt=ctx.config.runner_prompt,
        workspace_prompt=workspace_prompt,
        environments=environments,
        lease_id=lease_id,
        runner_id=ctx.config.runner_id,
        chunk_id=chunk_id,
        prior=prior_preamble,
    )
    prompt_prefix = rendered.text
    generation = _pending_generation(ctx, lease_id)
    preamble = WorkerPreamble(
        environments=environments,
        lease_id=lease_id,
        local_api_url=ctx.config.local_api_url,
        workspace_root=ctx.config.workspace_root,
        prompt_prefix=prompt_prefix,
        stdout_path=_stdout_path(ctx, lease_id, generation),
        stderr_path=_stderr_path(ctx, lease_id, generation),
        lease_token=lease_token,
    )
    try:
        handle = ctx.harness.spawn(
            envelope,
            preamble,
            session_hint=str(uuid.uuid4()),
            resume_from=resume_from,
            model=resolved_model,
            effort=resolved_effort,
        )
    except HarnessSpawnError as exc:
        # Surface the launch-time spawn failure (issue #125) then RE-RAISE: no worker started,
        # so the attempt was never recorded and the chunk simply retries next tick.
        _emit_command_failed(
            ctx,
            chunk_id=chunk_id,
            lease_id=lease_id,
            node_name=envelope.node.node_name,
            command="spawn harness worker",
            stderr_tail=str(exc),
        )
        raise
    ctx.store.record_spawn(
        lease_id,
        pid=handle.pid,
        process_start_time=handle.process_start_time,
        session_id=handle.session_id,
        spawned_at=now,
    )
    # Keyed on the HANDLE's session id — the authoritative continuation id (issue #149).
    # Written after the spawn, so a durable fingerprint always implies the prose was sent.
    ctx.store.record_session_preamble(handle.session_id, fingerprint=rendered.fingerprint, at=now)
    _CP_SPAWN_AFTER_SPAWN.reached()


#: The classification each `_fail_attempt` branch surfaces (issue #125). The
#: locally-paused defer branch surfaces nothing — a deferral is not an outcome.
_ATTEMPT_FAILED = ("warning", "attempt-failed")
_WORKER_LOST = ("critical", "worker-lost")
_ATTEMPT_ABANDONED = ("info", "attempt-abandoned")


def _failure_event_payload(
    lease: LeaseRecord, *, severity: str, kind: str, message: str, reason: str, via: str, stderr_tail: str = ""
) -> str:
    """The ``event.recorded`` payload one `_fail_attempt` branch surfaces (issue #125).

    ``detail`` carries the ``(reason, via)`` that classified it and the node it happened at,
    plus any captured stderr tail. A plain JSON string, so it rides the outbound buffer."""
    detail: dict[str, object] = {"via": via, "reason": reason, "node": lease.node_name}
    if stderr_tail:
        detail["stderr_tail"] = stderr_tail
    return json.dumps(
        {
            "severity": severity,
            "kind": kind,
            "chunk_id": lease.chunk_id,
            "lease_id": lease.lease_id,
            "node_name": lease.node_name,
            "message": message,
            "detail": detail,
        }
    )


def _emit_command_failed(
    ctx: LoopContext,
    *,
    chunk_id: str | None,
    lease_id: str | None,
    node_name: str | None,
    command: str,
    stderr_tail: str,
) -> None:
    """Surface a captured spawn/verify/env-prep command failure as a ``warning``
    ``command-failed`` operational event (issue #125, change L).

    Enqueued straight to the outbound buffer — it rides no closure and never alters the
    caller's control flow. The failing command and its stderr tail go in ``detail``."""
    payload = json.dumps(
        {
            "severity": "warning",
            "kind": "command-failed",
            "chunk_id": chunk_id,
            "lease_id": lease_id,
            "node_name": node_name,
            "message": f"command failed: {command}",
            "detail": {"command": command, "stderr_tail": stderr_tail[-2000:] if stderr_tail else ""},
        }
    )
    ctx.store.enqueue_outbound(
        kind=EVENT_RECORDED, chunk_id=chunk_id, lease_id=lease_id, payload=payload, created_at=ctx.clock.now()
    )


def _fail_attempt(ctx: LoopContext, lease: LeaseRecord, *, reason: str, via: str) -> None:
    """Close a failed attempt, then requeue at the node or escalate per the budget.

    An escalation is a one-way door this same tick's flush cannot retract, so the
    exhausted-retries branch re-asks the ownership question first (blizzard#38) and defers
    entirely while locally paused (issue #45). The requeue branch needs no such gate."""
    now = ctx.clock.now()
    if lease.pid is not None:
        ctx.process.kill(lease.pid)  # best-effort hygiene; the epoch fence is the guarantee

    # Best-effort: a worker that never crashed to stderr wrote no tail, which is the
    # ordinary case.
    stderr_tail = _stderr_tail(ctx, lease)

    # attempt_count includes this lease, and a first attempt is not a retry.
    retried = ctx.store.attempt_count(lease.chunk_id, lease.node_id) - 1
    if retried < lease.retries_max:
        # Retry: enqueued ATOMICALLY with the closure it describes (issue #125).
        severity, kind = _ATTEMPT_FAILED
        ctx.store.record_closure(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            reason=reason,
            closed_at=now,
            event_kind=EVENT_RECORDED,
            event_payload=_failure_event_payload(
                lease,
                severity=severity,
                kind=kind,
                message=f"attempt failed, retrying — {reason} (via {via})",
                reason=reason,
                via=via,
                stderr_tail=stderr_tail,
            ),
        )
        _requeue(ctx, lease)
        return
    if _reassigned_or_detached(ctx, lease):
        # Emitted HERE rather than in the shared abandon helper, which the ordinary detach
        # sweep also reaches and which must stay silent.
        severity, kind = _ATTEMPT_ABANDONED
        ctx.store.enqueue_outbound(
            kind=EVENT_RECORDED,
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
            payload=_failure_event_payload(
                lease,
                severity=severity,
                kind=kind,
                message=f"attempt abandoned — chunk reassigned ({reason}, via {via})",
                reason=reason,
                via=via,
                stderr_tail=stderr_tail,
            ),
            created_at=now,
        )
        _abandon_reassigned(ctx, lease, killed=True, via=via)
        return
    if ctx.store.local_paused(ctx.config.runner_id):
        # Deliberate deferral, not a surfaced failure — emit nothing (issue #125).
        _log.info(
            "escalation deferred — locally paused",
            runner_id=ctx.config.runner_id,
            via=via,
            chunk_id=lease.chunk_id,
            lease_id=lease.lease_id,
        )
        return
    # Escalate: enqueued ATOMICALLY with the closure it describes (issue #125).
    severity, kind = _WORKER_LOST
    ctx.store.record_closure(
        lease_id=lease.lease_id,
        chunk_id=lease.chunk_id,
        node_id=lease.node_id,
        reason=_ESCALATED,
        closed_at=now,
        event_kind=EVENT_RECORDED,
        event_payload=_failure_event_payload(
            lease,
            severity=severity,
            kind=kind,
            message=f"worker lost — retries exhausted ({reason}, via {via})",
            reason=reason,
            via=via,
            stderr_tail=stderr_tail,
        ),
    )
    _escalate(ctx, lease)


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
            _release_all(ctx, chunk_id)
            return
        except HubClientError:
            return  # hub unreachable — the binding is durable; retry next tick
        ctx.store.set_route_token(chunk_id, token=rekeyed.route_token, at=ctx.clock.now())
    try:
        envelope = ctx.hub.get_envelope(chunk_id)
    except ChunkNotFoundError:
        _log.warning("hub reports adopted chunk unknown — releasing envs", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
        return
    except HubClientError:
        return  # hub unreachable — the binding is durable; retry next tick
    _log.info("adopting interrupted claim — spawning current node", chunk_id=chunk_id)
    _spawn_attempt(ctx, chunk_id, envelope, _bindings_as_environments(bindings), via="adopt")


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
        _release_all(ctx, chunk_id)
        return
    except HubClientError:
        return  # hub unreachable — the requeue fact is durable; retry next tick
    _log.info("resuming requeued chunk — spawning current node", chunk_id=chunk_id)
    _spawn_attempt(ctx, chunk_id, envelope, _bindings_as_environments(bindings), via="requeue-resume")


def _reclaim_interrupted(ctx: LoopContext, chunk_id: str, bindings: list[EnvBindingRecord]) -> None:
    """Complete a claim whose hub POST never landed — claim now, reusing the held binding.

    The environment was bound but the claim never landed, so the chunk still reads ``ready``.
    The route is claimed with the environment already held rather than re-acquired; a lost race
    releases the binding."""
    envs = _bindings_as_environments(bindings)
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
        _release_all(ctx, chunk_id)
        return
    if outcome.conflict is not None or outcome.claimed is None:
        _log.info("interrupted claim lost the race — releasing binding", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
        return
    _log.info("re-claimed interrupted chunk — spawning current node", chunk_id=chunk_id)
    # A reclaim is a fresh claim, so its token overwrites whatever this chunk's row held
    # before — a fresh claim always wins (issue #84a).
    ctx.store.set_route_token(chunk_id, token=outcome.claimed.route_token, at=ctx.clock.now())
    _spawn_attempt(ctx, chunk_id, outcome.claimed.envelope, envs, via="reclaim")


def _requeue(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Re-attempt the node in the same environments — new session, new lease, fresh epoch.

    The prior attempt's lease is already closed before this runs, so a 404 here leaves no active
    lease behind for any later sweep to clean up — the binding would be held forever. It is
    therefore released here rather than retried (blizzard#9)."""
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings:
        _log.warning("requeue with no bound env — cannot re-spawn", chunk_id=lease.chunk_id)
        return
    try:
        envelope = ctx.hub.get_envelope(lease.chunk_id)  # idempotent re-read
    except ChunkNotFoundError:
        _log.warning("hub reports chunk unknown at requeue — releasing envs", chunk_id=lease.chunk_id)
        _release_all(ctx, lease.chunk_id)
        return
    except HubClientError:
        return  # the closed attempt is durable; FILL/ADVANCE re-drives next tick
    _log.info("requeuing at node", chunk_id=lease.chunk_id, node=lease.node_name)
    _spawn_attempt(ctx, lease.chunk_id, envelope, _bindings_as_environments(bindings), via="requeue")


def _escalate(ctx: LoopContext, lease: LeaseRecord, *, reason: str = "retries exhausted") -> None:
    """Park the chunk needs-human at the hub, envs held for takeover.

    The escalation rides the outbound buffer as an ``escalation.recorded`` fact carrying two
    takeover strings — the wrapped entry point and the raw pasteable fallback.
    """
    now = ctx.clock.now()
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    takeover = ""
    wrapped_takeover = ""
    if lease.session_id is not None and bindings:
        # Composed from the lease's own stamps (issue #144), so a takeover lands in exactly
        # the configuration the parked session ran with, never a fresh resolution.
        takeover = ctx.harness.resume_command(
            bindings[0].workdir,
            lease.session_id,
            model=lease.resolved_model,
            effort=lease.resolved_effort,
        )
        # Wrapped implies raw, never the reverse (blizzard-context:/domain/humans.md).
        # `bindings` is checked explicitly above — an empty one is not provably unreachable.
        if ctx.config.runner_dir:
            wrapped_takeover = wrapped_takeover_command(lease.chunk_id, ctx.config.runner_dir)
    payload = json.dumps(
        {
            "chunk_id": lease.chunk_id,
            "epoch": lease.epoch,
            "takeover_command": takeover,
            "wrapped_takeover_command": wrapped_takeover,
            "route_token": ctx.store.route_token(lease.chunk_id),  # issue #84a
        }
    )
    ctx.store.enqueue_outbound(
        kind=ESCALATION_RECORDED, chunk_id=lease.chunk_id, lease_id=lease.lease_id, payload=payload, created_at=now
    )
    _log.info(
        f"escalated to needs-human — {reason}", chunk_id=lease.chunk_id, takeover=takeover, wrapped=wrapped_takeover
    )


def _park_on_ask(ctx: LoopContext, lease: LeaseRecord, ask: AskRecord) -> None:
    """Park the chunk on a question: forward it to the hub and stop the reap clock.

    The local park fact stops the reap clock and keeps the lease from being re-parked or judged;
    env bindings stay held so the session is warm for the resume. No retry is consumed.
    """
    now = ctx.clock.now()
    _record_worker_usage(ctx, lease, ctx.store.bindings_for_chunk(lease.chunk_id))
    payload = json.dumps(
        {
            "question_id": ask.question_id,
            "chunk_id": lease.chunk_id,
            "node_id": lease.node_id,
            "session_id": ask.session_id or lease.session_id,
            "epoch": lease.epoch,
            "question": ask.question,
            "options": ask.options,
            "asked_at": iso_utc(ask.asked_at),
            "route_token": ctx.store.route_token(lease.chunk_id),  # issue #84a
        }
    )
    ctx.store.enqueue_outbound(
        kind=QUESTION_ASKED, chunk_id=lease.chunk_id, lease_id=lease.lease_id, payload=payload, created_at=now
    )
    ctx.store.record_park(lease_id=lease.lease_id, chunk_id=lease.chunk_id, question_id=ask.question_id, parked_at=now)
    _log.info("chunk parked on question", chunk_id=lease.chunk_id, question_id=ask.question_id)


def _resume_if_answered(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Poll a parked lease's question; on an answer, resume the dormant session.

    Crash-safe and re-runnable: an unanswered question polls as a no-op and the reap clock stays
    stopped. Once answered the agent is reconstituted under the same session, lease and step.
    """
    if _spawn_suppressed(ctx, via="answer-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
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
        stdout_path=_stdout_path(ctx, lease.lease_id, _pending_generation(ctx, lease.lease_id)),
        preamble=_resume_preamble(ctx, lease, bindings),
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
    ctx.store.enqueue_outbound(
        kind=ANSWER_DELIVERED,
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        payload=json.dumps({"chunk_id": lease.chunk_id, "question_id": park.question_id}),
        created_at=now,
    )
    _log.info("resumed dormant session with answer", chunk_id=lease.chunk_id, question_id=park.question_id, pid=pid)


def _resume_if_unpaused(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Poll a pause-parked lease's chunk; once the operator resumes it, restart its session (#46).

    Same lease, epoch and session; only ``pid``/``process_start_time`` are rewritten, so **no
    retry is consumed** — the pause cost the chunk a process, not an attempt. An **ask-parked**
    lease returns early even once unpaused, so a lift never conjures an absent answer."""
    if _spawn_suppressed(ctx, via="pause-resume", chunk_id=lease.chunk_id, lease_id=lease.lease_id):
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
        stdout_path=_stdout_path(ctx, lease.lease_id, _pending_generation(ctx, lease.lease_id)),
        preamble=_resume_preamble(ctx, lease, bindings),
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


def _missing_produces(
    envelope: NodeEnvelope, git_artifacts: list[SubmittedArtifact], attachments: dict[str, str]
) -> list[ProducesEntry]:
    """Every `produces:` spec this attempt does not yet cover (issue #143) — the nudge-worthy
    set, in the envelope's own declaration order rather than attachment order. Returns the
    unmet specs themselves, not just their names, since each spec's `kind` names a different
    declaration verb. Evaluated by the shared ``produces_coverage`` predicate, so this and the
    upstream backstop cannot drift apart."""

    attached = [
        SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=content, attached=True)
        for name, content in attachments.items()
    ]
    return produces_coverage(envelope.node.produces, git_artifacts + attached)


def _nudge_message(missing: list[ProducesEntry]) -> str:
    """The nudge resume's message (issues #113, #143): one `#`-prefixed comment line per unmet
    `produces:` spec, naming the kind-appropriate declaration verb and its positionals. Same
    inert `#` framing as the other resume messages."""

    lines = ["# This node's `produces:` still needs an explicit submission:"]
    for spec in missing:
        if spec.kind is ArtifactKind.GIT_COMMIT:
            lines.append(
                f"#   - {spec.name} (git_commit): push your branch, then run "
                f"`blizzard runner artifact commit --repo <repo> --branch <branch> "
                f"--commit <sha>` for each repo you touched (`<repo>` is its name in "
                f"the environment's manifest; add `--env <id>` when the chunk holds "
                f"more than one environment)."
            )
        else:
            lines.append(
                f"#   - {spec.name} (asset): run `blizzard runner artifact create "
                f"--name {spec.name}` with the content on stdin."
            )
    lines.append("# Do this before this attempt is judged done.")
    return "\n".join(lines)


def _collect_asset_artifacts(
    envelope: NodeEnvelope,
    git_artifacts: list[SubmittedArtifact],
    assessment: str,
    attachments: dict[str, str],
) -> list[SubmittedArtifact]:
    """Emit an asset artifact per produced name no git commit covers.

    An explicit attachment wins over the worker's judgement assessment and is marked
    ``attached=True`` — the provenance a multi-asset node needs to tell its artifacts apart
    rather than aliasing them all to one assessment (#90)."""

    covered = {a.name for a in git_artifacts}
    submitted: list[SubmittedArtifact] = []
    for spec in envelope.node.produces:
        if spec.kind is ArtifactKind.GIT_COMMIT:
            continue
        name = spec.name
        if name in covered:
            continue
        if name in attachments:
            submitted.append(
                SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=attachments[name], attached=True)
            )
        else:
            submitted.append(SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=assessment))
    return submitted


def _verify_and_collect_git_commits(
    ctx: LoopContext,
    lease: LeaseRecord,
    bindings: list[EnvBindingRecord],
    already_declared: dict[tuple[str, str], GitCommitDeclarationRecord] | None = None,
) -> tuple[list[SubmittedArtifact], dict[tuple[str, str], GitCommitDeclarationRecord]]:
    """Confirm each of this lease's declared git commits read-only against the origin its
    declaring environment's manifest names (issue #143). Never mutates git and never infers a
    branch off residue. Spans **every** bound environment, since the declaration key carries the
    env. A declaration that does not verify is reported, never silently dropped, and
    ``already_declared`` skips one this attempt already resolved."""

    already_declared = already_declared or {}
    declarations = ctx.store.git_commit_declarations_for_lease(lease.lease_id)
    origins = _repo_origins(ctx, bindings)
    submitted: list[SubmittedArtifact] = []
    for key, declared in declarations.items():
        if already_declared.get(key) == declared:
            continue
        env_id, repo = key
        origin_url = origins.get(key)
        if origin_url is None:
            # Reaching here means the manifest changed under the lease, not a worker typo.
            # An unresolvable origin means this commit cannot be delivered — say so.
            _emit_command_failed(
                ctx,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                node_name=lease.node_name,
                command=f"resolve origin for --repo {repo!r} in environment {env_id!r}",
                stderr_tail=(
                    f"environment {env_id!r} no longer lists repo {repo!r}; "
                    f"it lists {sorted(name for (env, name) in origins if env == env_id)}"
                ),
            )
            continue
        try:
            verified = ctx.worktree_git.verify(origin_url, declared.branch, declared.commit)
        except WorktreeGitError as exc:
            _emit_command_failed(
                ctx,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                node_name=lease.node_name,
                command=f"git ls-remote {origin_url} {declared.branch} (--repo {repo!r}, --env {env_id!r})",
                stderr_tail=str(exc),
            )
            continue
        if not verified:
            _emit_command_failed(
                ctx,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
                node_name=lease.node_name,
                command=f"git ls-remote {origin_url} {declared.branch} (--repo {repo!r}, --env {env_id!r})",
                stderr_tail=(
                    f"declared commit {declared.commit} is not what branch {declared.branch!r} "
                    f"points at on {origin_url} — push the branch (or re-declare the sha "
                    f"`git rev-parse HEAD` actually produced) and declare it again"
                ),
            )
            continue
        submitted.append(
            SubmittedArtifact(
                name=repo,
                kind=ArtifactKind.GIT_COMMIT,
                forge=origin_url,
                repo=repo,
                branch_name=declared.branch,
                commit_hash=declared.commit,
            )
        )
    return submitted, declarations


def _repo_origins(ctx: LoopContext, bindings: list[EnvBindingRecord]) -> dict[tuple[str, str], str]:
    """``{(environment_id, repo): origin_url}`` across every bound environment.

    The provider is the authority on both which repos an env holds and where each pushes, so
    this is a lookup, never a path guessed from a workdir or a cwd."""
    origins: dict[tuple[str, str], str] = {}
    for binding in bindings:
        for repo in ctx.provider.repos(binding.environment_id):
            origins[(binding.environment_id, repo.name)] = repo.origin_url
    return origins


def _release_all(ctx: LoopContext, chunk_id: str) -> None:
    """Release every held environment at the chunk's tenure end, and clean up every
    lease this chunk ever minted's per-generation usage-stdout files (issue #58) —
    bounded, one file per attempt ever made under each lease, no longer needed once
    its usage facts are durable."""
    now = ctx.clock.now()
    for binding in ctx.store.bindings_for_chunk(chunk_id):
        ctx.provider.release(binding.environment_id)
        ctx.store.record_release(chunk_id=chunk_id, environment_id=binding.environment_id, released_at=now)
    for lease_id in ctx.store.lease_ids_for_chunk(chunk_id):
        _cleanup_stdout(ctx, lease_id)


def _cleanup_stdout(ctx: LoopContext, lease_id: str) -> None:
    """Remove every one of a lease's per-generation stdout files, if any.

    Bounded to the durably recorded generation count plus one: the un-armable spawn-record gap
    can leave a file for a generation whose own ``record_spawn`` never landed. A missing file at
    any of those generations is a no-op."""
    if not ctx.config.worker_stdout_dir:
        return
    for generation in range(1, ctx.store.lease_generation(lease_id) + 2):
        with contextlib.suppress(OSError):
            os.remove(_stdout_path(ctx, lease_id, generation))


def _release_acquired(ctx: LoopContext, acquired: list[AcquiredEnvironment]) -> None:
    """Release just-acquired (unbound) environments after a lost claim."""
    for a in acquired:
        ctx.provider.release(a.environment_id)


def _release_binding(ctx: LoopContext, chunk_id: str, acquired: list[AcquiredEnvironment]) -> None:
    """Undo a just-recorded binding whose claim never landed — release the fact and the env.

    The binding is written before the hub claim, so a claim that fails to send or
    loses the race must retract both the local binding fact and the provider allocation,
    leaving the chunk exactly as if it had never been touched (it stays ``ready``)."""
    now = ctx.clock.now()
    for a in acquired:
        ctx.store.record_release(chunk_id=chunk_id, environment_id=a.environment_id, released_at=now)
        ctx.provider.release(a.environment_id)


def _bindings_as_environments(bindings: list[EnvBindingRecord]) -> list[AcquiredEnvironment]:
    return [AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in bindings]


def _elicitation_tail(envelope: NodeEnvelope) -> str:
    """The engine-generated ``<Choice>`` elicitation appended to the judgement prompt.

    Emitted as ``#``-prefixed lines so the tail is inert whether the prompt is read as prose
    or executed as a script.
    """
    lines = ["", "", "# Select exactly one outcome and reply with <Choice>name</Choice>:"]
    for choice in envelope.node.choices:
        lines.append(f"#   - {choice.name}: {choice.description}")
    return "\n".join(lines)


def _checks_block(results: list[CheckResultRecord]) -> str:
    """The runner-executed check results injected into the judgement prompt (issue #114).

    One ``#``-prefixed line per check with its command and ``PASS``/``FAIL``, so the worker
    judges against mechanical truth. A failed check additionally shows its output tail.
    """
    if not results:
        return ""
    lines = ["", "", "# Checks (runner-executed at your exit — judge against these, not your recollection):"]
    for r in results:
        lines.append(f"#   [{'PASS' if r.passed else 'FAIL'}] {r.command}")
        if not r.passed:
            for tail_line in r.output_tail.strip().splitlines():
                lines.append(f"#       {tail_line}")
    return "\n".join(lines)


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
