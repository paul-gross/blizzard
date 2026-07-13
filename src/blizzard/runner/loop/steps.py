"""The reconciliation step functions — REAP → PULL → FILL → ADVANCE (``bzh:steppable-loop``).

Each is an individually callable function of a :class:`LoopContext`; the tick driver
and the ``blizzard runner tick`` CLI verb call them in order. Every step is
idempotent and holds no state of its own — all facts live in the runner store, so a
crash mid-tick followed by a restart re-runs the tick harmlessly (D-023/D-028), and
startup recovery is just REAP running first.

The dead-worker split (design/runner/loop.md): a **session-bearing** worker whose
process has *exited* is a *done declaration* (exit-is-done, D-055) and belongs to
ADVANCE — its judgement reply, or its absence (D-009), tells a done from a crash.
REAP handles the residue ADVANCE structurally cannot judge: a lease whose worker
never reached spawn-return (no pid/session — killed mid-FILL), and a **stalled-but-
alive** worker whose heartbeat has gone stale (a live pid that stopped making tool
calls, so it stopped beating — D-069). Both the verdict-less-exit failure (ADVANCE)
and the reaped orphan/stall (REAP) route through one ``requeue-or-escalate`` decision
keyed on the node's retry budget (D-078/D-009). Liveness is heartbeat-freshness for a
live pid, plus (pid, start_time) to survive pid reuse.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from blizzard.foundation.ids import LEASE_PREFIX, mint
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.environments.provider import AcquiredEnvironment, WorkspaceAcquisitionError
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import HubClientError
from blizzard.runner.store.repository import AskRecord, BufferedFact, EnvBindingRecord, LeaseRecord, NewLease
from blizzard.wire.completion import CompletionSubmission, SubmittedArtifact
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeEnvelope
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    LEASE_MINTED,
    QUESTION_ASKED,
    RunnerFact,
    RunnerFactBatch,
)
from blizzard.wire.route import RouteClaim

_log = get_logger("blizzard.runner.loop")

# Closure reasons (lease_closures.reason).
_TRANSITIONED = "transitioned"
_REAPED = "reaped"
_FAILED = "failed"
_ESCALATED = "escalated"

# Outbound-buffer fact kinds (design/runner/store.md). ``completion.submitted`` is the
# runner-local kind whose flush drives the apply-response; the two hub-fact kinds
# (LEASE_MINTED / ESCALATION_RECORDED) flush to POST /events (D-069/D-044).
_COMPLETION_KIND = "completion.submitted"

# The env count a solo chunk wants; batching (K>1) is parked (design/runner/environments.md).
_SOLO_ENV_COUNT = 1

#: REAP's staleness threshold (design/runner/loop.md). Deliberately **conservative**:
#: heartbeats ride tool calls, so the threshold is bounded below by the longest tool
#: call a healthy worker makes — one long test run must never read as a stall. A live
#: worker whose last heartbeat is older than this has stopped making tool calls and is
#: reaped as stalled (D-078). ~1h; the open-question constant (decisions/open-questions.md).
HEARTBEAT_STALENESS_THRESHOLD = timedelta(hours=1)


# --------------------------------------------------------------------------- #
# REAP
# --------------------------------------------------------------------------- #


def reap(ctx: LoopContext) -> None:
    """Expire leases whose worker is gone or **stalled** (design/runner/loop.md).

    Three cases end an attempt here (each a failed execution attempt, D-078 —
    requeue or escalate):

    * **orphan** — a lease with no recorded pid/session: minted at FILL but never
      spawned (a crash in the mint→spawn window). ADVANCE structurally cannot judge it.
    * **stalled-but-alive** — a live worker whose last heartbeat is older than the
      conservative :data:`HEARTBEAT_STALENESS_THRESHOLD`. Heartbeats ride tool calls
      (D-069), so a worker that stops progressing stops beating; there is no separate
      stall detector. REAP kills it (``_fail_attempt`` does the best-effort kill) — the
      epoch fence, not the kill, is what guarantees the zombie cannot deliver.

    A session-bearing worker whose process has **exited** is *not* reaped here: exit is
    the done declaration (D-055), so it belongs to ADVANCE, which resumes the session
    to tell a real completion from a crash. The conservative threshold is what keeps
    the two apart — a worker that exited cleanly still carries a fresh final heartbeat,
    so REAP never preempts ADVANCE's judgement of it.
    """
    now = ctx.clock.now()
    parked = ctx.store.parked_lease_ids()
    for lease in ctx.store.list_active_leases():
        if lease.lease_id in parked:
            # Dormant on a question (ask-and-exit): no live worker to stall, so the
            # reap clock is stopped — a parked chunk is never reaped for inactivity
            # ([ask-answer.md]). The answer's arrival resumes it (ADVANCE).
            continue
        if lease.pid is None or lease.session_id is None:
            _log.info("reaping unspawned lease", lease_id=lease.lease_id, chunk_id=lease.chunk_id)
            _fail_attempt(ctx, lease, reason=_REAPED)
            continue
        if not ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # exited — ADVANCE's (exit-is-done, D-055)
        if _is_heartbeat_stale(ctx, lease, now):
            _log.info("reaping stalled worker", lease_id=lease.lease_id, chunk_id=lease.chunk_id, pid=lease.pid)
            _fail_attempt(ctx, lease, reason=_REAPED)
        # A live, beating worker runs on.


def _is_heartbeat_stale(ctx: LoopContext, lease: LeaseRecord, now: datetime) -> bool:
    """True iff the lease's last activity is older than the staleness threshold.

    Last activity is the newest heartbeat, or — before the worker's first tool call —
    the lease's own creation instant, so a freshly spawned worker is never read as
    stalled inside the threshold window.
    """
    last = ctx.store.latest_heartbeat(lease.lease_id) or lease.created_at
    return now - _as_utc(last) > HEARTBEAT_STALENESS_THRESHOLD


def _as_utc(value: datetime) -> datetime:
    """Read a stored timestamp back as UTC-aware — sqlite drops the tzinfo the clock set."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# PULL
# --------------------------------------------------------------------------- #


def pull(ctx: LoopContext) -> None:
    """Exchange facts with the hub (outbound-only, D-012): drain the outbound buffer.

    Store-and-forward always (D-069): every hub-bound fact was written to the buffer
    at mint with a per-runner monotonic seq, and this is the single flusher that
    drains it — FIFO, so a ``lease.minted`` always precedes the completion minted under
    it. A completion's flush is special: its apply-response carries the chunk's next
    node envelope (D-072), so the flusher drives the continue-in-place here. A
    transport failure stops the drain (the buffer is the only ordered path — a later
    fact must not overtake a stuck earlier one) and the backlog flushes next tick; an
    outage is just a bigger backlog.
    """
    flush_outbound(ctx)


def flush_outbound(ctx: LoopContext) -> None:
    """Drain the outbound buffer in FIFO order until a fact fails to deliver (D-069)."""
    for fact in ctx.store.pending_outbound():
        if not _flush_one(ctx, fact):
            break  # transport failure — stop; strict FIFO, retry the backlog next tick


def _flush_one(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Deliver one buffered fact. Return False on a transport failure (stop the drain)."""
    if fact.kind == _COMPLETION_KIND:
        return _flush_completion(ctx, fact)
    return _flush_hub_fact(ctx, fact)


def _flush_hub_fact(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Push a ``lease.minted`` / ``escalation.recorded`` fact to POST /events (D-069)."""
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
        # A contract rejection (unknown kind) is not idempotency — surface it, but do
        # not wedge the FIFO drain on a fact the hub will never accept: ack and move on.
        _log.error("hub rejected buffered fact", seq=fact.seq, kind=fact.kind)
    ctx.store.ack_outbound(fact.seq, acked_at=ctx.clock.now())
    return True


def _flush_completion(ctx: LoopContext, fact: BufferedFact) -> bool:
    """Submit a buffered completion and drive its apply-response (D-036/D-072/D-090).

    Idempotent by construction: the hub's completion apply is epoch-idempotent (a
    re-applied completion returns its original outcome without a second transition,
    D-090), and the runner acts on the response only while the lease is still active —
    a re-flush after a lost ack finds the lease closed and simply clears the buffer.
    """
    payload = json.loads(fact.payload)
    submission = CompletionSubmission.model_validate(payload["submission"])
    try:
        response = ctx.hub.submit_completion(fact.chunk_id or "", submission)
    except HubClientError:
        return False  # completion stays durable in the buffer; the mid-node worker is unaffected

    ctx.store.ack_outbound(fact.seq, acked_at=ctx.clock.now())
    lease = ctx.store.active_lease(fact.lease_id or "")
    if lease is None:
        # Already advanced on an earlier flush whose ack was lost (D-090) — nothing to do.
        return True
    _consume_apply_response(ctx, lease, response)
    return True


def _consume_apply_response(ctx: LoopContext, lease: LeaseRecord, response: ApplyResponse) -> None:
    """Record the closure and continue in place per the hub's apply-response (D-072)."""
    if response.outcome == ApplyOutcome.FAILURE:
        # A semantic rejection — a stale-epoch (zombie) or terminal completion. The
        # attempt failed; requeue or escalate. The chunk never advanced and never
        # entered the merge queue (the hub fenced it before any write, D-007).
        _log.warning("completion rejected on flush", chunk_id=lease.chunk_id, detail=response.detail or "")
        _fail_attempt(ctx, lease, reason=_FAILED)
        return
    now = ctx.clock.now()
    ctx.store.record_closure(
        lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=_TRANSITIONED, closed_at=now
    )
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    _apply_response(ctx, lease, response.outcome, response.next_envelope, bindings)


# --------------------------------------------------------------------------- #
# FILL
# --------------------------------------------------------------------------- #


def fill(ctx: LoopContext) -> None:
    """Keep the fleet busy: peek → acquire → claim-by-route → bind → spawn (D-080).

    Since D-080, FILL is where work is claimed. Open agent slots are
    ``MAX_AGENTS - active_leases``; for each, peek the ready queue, acquire the
    chunk's environments (all-or-nothing), and POST the complete route. A 409 is
    race-second-place — release the bindings and move on. The winning claim carries
    the first node envelope, so the worker starts without a second round-trip.
    """
    slots = ctx.config.max_agents - len(ctx.store.list_active_leases())
    for _ in range(max(slots, 0)):
        if not _fill_one(ctx):
            break


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
    try:
        acquired = ctx.provider.acquire(entry.chunk_id, _SOLO_ENV_COUNT, held)
    except WorkspaceAcquisitionError:
        _log.info("acquire refused — env-bound this tick", chunk_id=entry.chunk_id)
        return False  # env capacity exhausted; the chunk waits

    claim = RouteClaim(
        chunk_id=entry.chunk_id,
        runner_id=ctx.config.runner_id,
        workspace_id=ctx.config.workspace_id,
        environment_ids=[a.environment_id for a in acquired],
    )
    try:
        outcome = ctx.hub.claim_route(claim)
    except HubClientError:
        _release_acquired(ctx, acquired)
        return False
    if outcome.conflict is not None or outcome.claimed is None:
        _log.info("route claim lost the race", chunk_id=entry.chunk_id)
        _release_acquired(ctx, acquired)
        return True  # someone else took it; peek fresh next iteration

    now = ctx.clock.now()
    for a in acquired:
        ctx.store.record_binding(
            chunk_id=entry.chunk_id, environment_id=a.environment_id, workdir=a.workdir, bound_at=now
        )
    _spawn_attempt(ctx, entry.chunk_id, outcome.claimed.envelope, acquired)
    return True


# --------------------------------------------------------------------------- #
# ADVANCE
# --------------------------------------------------------------------------- #


def advance(ctx: LoopContext) -> None:
    """Judge finished workers and move chunks through the graph (D-025/D-027).

    Two responsibilities: (a) a session-bearing worker whose process has exited is a
    done declaration — resume it with the judgement prompt, parse the ``<Choice>``,
    push its artifacts, and **buffer** the epoch-fenced completion (the flusher in
    PULL delivers it and drives the apply-response, D-069); (b) a chunk held at a hub
    node (envs bound, no active lease) is polled for the hub's terminal outcome — a
    landed delivery releases its environments (D-066).

    A worker whose completion is already buffered is skipped: the verdict is elicited
    exactly once, then the chunk waits at its node boundary for the flush (D-069).
    """
    pending_completions = ctx.store.pending_completion_lease_ids()
    parked = ctx.store.parked_lease_ids()
    for lease in ctx.store.list_active_leases():
        if lease.pid is None or lease.session_id is None:
            continue  # REAP's residue
        if lease.lease_id in pending_completions:
            continue  # completion elicited, awaiting flush — the node boundary (D-069)
        if lease.lease_id in parked:
            _resume_if_answered(ctx, lease)  # dormant on a question — resume on the answer
            continue
        if ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # worker still running
        _advance_exited_worker(ctx, lease)

    for chunk_id in ctx.store.live_tenure_chunk_ids():
        if ctx.store.active_lease_for_chunk(chunk_id) is None:
            _poll_hub_node(ctx, chunk_id)


def _advance_exited_worker(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Park on an open ask, else elicit the verdict and buffer the completion (D-069/D-009)."""
    if lease.session_id is None:
        return  # not spawned — REAP's residue (guarded by the caller too)

    # Ask-and-exit ([ask-answer.md]): a worker that exited holding an unforwarded ask
    # parked on a question — forward it and park, no verdict, no retry consumed. This is
    # what D-009 turns on: an exit with an open ask is a park; an exit with neither is a
    # failure. The park fact stops REAP's clock and makes the chunk derive waiting_on_human.
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

    # 1. Push produced branches to their forge origins BEFORE submitting (D-026).
    artifacts = _push_and_collect_artifacts(ctx, bindings)

    # 2. Elicit the verdict via the judgement resume (D-038). A dead worker whose
    #    session cannot answer a parseable <Choice> is a failure (D-009).
    prompt = (envelope.judgement_prompt or "") + _elicitation_tail(envelope)
    # The adapter works in a directory; the runner resolves the provider-returned
    # workdir from the binding and supplies it (design/runner/environments.md).
    output = ctx.harness.judge(bindings[0].workdir, lease.session_id, prompt)
    choice = ctx.harness.parse_verdict(output)
    if choice is None:
        _log.warning("verdict-less judgement — failing attempt", chunk_id=lease.chunk_id, lease_id=lease.lease_id)
        _fail_attempt(ctx, lease, reason=_FAILED)
        return

    # 2b. Harvest the node's asset artifacts (D-026): a node that `produces` a name no
    #     pushed git commit covers (the review node's `findings`) emits the worker's
    #     assessment as that asset's content, which a fail judgement carries back into
    #     the build envelope latest-by-epoch (design/workflow-engine.md review node).
    artifacts += _collect_asset_artifacts(envelope, artifacts, ctx.harness.parse_assessment(output))

    # 3. Buffer the completion — one atomic, epoch-fenced write (D-036), delivered by
    #    the flusher (D-069). The buffer entry names the lease so the flush drives its
    #    apply-response; ADVANCE will skip this lease until the flush closes it.
    submission = CompletionSubmission(
        choice=choice,
        epoch=lease.epoch,
        runner_id=ctx.config.runner_id,
        from_node_id=lease.node_id,
        check_results=[],  # in-session check assessment is P7; the model carries them (D-077)
        artifacts=artifacts,
    )
    payload = json.dumps({"submission": submission.model_dump(mode="json")})
    ctx.store.enqueue_outbound(
        kind=_COMPLETION_KIND,
        chunk_id=lease.chunk_id,
        lease_id=lease.lease_id,
        payload=payload,
        created_at=ctx.clock.now(),
    )
    _log.info("completion buffered", chunk_id=lease.chunk_id, lease_id=lease.lease_id, choice=choice)


def _apply_response(
    ctx: LoopContext,
    lease: LeaseRecord,
    outcome: ApplyOutcome,
    next_envelope: NodeEnvelope | None,
    bindings: list[EnvBindingRecord],
) -> None:
    """Act on the apply-response: continue in place, hold at a hub node, or finish (D-072)."""
    if outcome == ApplyOutcome.NEXT and next_envelope is not None:
        envs = _bindings_as_environments(bindings)
        _spawn_attempt(ctx, lease.chunk_id, next_envelope, envs)
    elif outcome == ApplyOutcome.HUB_NODE_TAKEN:
        _log.info("hub node took over — holding envs until terminal", chunk_id=lease.chunk_id)
    elif outcome == ApplyOutcome.DONE:
        _release_all(ctx, lease.chunk_id)
    elif outcome == ApplyOutcome.PARKED_AT_GATE:
        _log.info("chunk parked at human gate", chunk_id=lease.chunk_id)  # waiting_on_human (P7)


def _poll_hub_node(ctx: LoopContext, chunk_id: str) -> None:
    """Poll a hub-node-held chunk for its terminal outcome; release on landed (D-066)."""
    try:
        detail = ctx.hub.get_chunk(chunk_id)
    except HubClientError:
        return
    if detail.status == ChunkStatus.DONE:
        _log.info("delivery landed — releasing envs", chunk_id=chunk_id)
        _release_all(ctx, chunk_id)
    # A conflict routing back to a runner node (D-058) reappears as a fresh envelope
    # the next claim/advance picks up; that recovery cycle is P7.


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _spawn_attempt(
    ctx: LoopContext, chunk_id: str, envelope: NodeEnvelope, environments: list[AcquiredEnvironment]
) -> None:
    """Mint a fresh-epoch lease and spawn a headless worker for a node-step (D-082/D-092)."""
    now = ctx.clock.now()
    epoch = ctx.store.latest_epoch(chunk_id) + 1
    lease_id = mint(LEASE_PREFIX, ctx.clock)
    node = envelope.node
    retries_max = node.retries_max if node.retries_max is not None else ctx.config.default_retries_max
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
            created_at=now,
        )
    )
    # The lease is a hub-bound fact (D-044): buffer it so the flusher reports it up to
    # POST /events, ahead of any completion minted under it (FIFO, D-069). It is the
    # fence input the hub's completion check consumes — the runner's mint keeps the
    # hub's latest epoch in lockstep across a build -> review chunk, and a requeue's mint
    # closes an escalation by supersession (D-035/D-067).
    ctx.store.enqueue_outbound(
        kind=LEASE_MINTED,
        chunk_id=chunk_id,
        lease_id=lease_id,
        payload=json.dumps({"chunk_id": chunk_id, "epoch": epoch}),
        created_at=now,
    )
    preamble = WorkerPreamble(environments=environments, lease_id=lease_id, local_api_url=ctx.config.local_api_url)
    handle = ctx.harness.spawn(envelope, preamble, session_hint=str(uuid.uuid4()))
    ctx.store.record_spawn(
        lease_id, pid=handle.pid, process_start_time=handle.process_start_time, session_id=handle.session_id
    )


def _fail_attempt(ctx: LoopContext, lease: LeaseRecord, *, reason: str) -> None:
    """Close a failed attempt, then requeue at the node or escalate per the budget (D-078/D-009)."""
    now = ctx.clock.now()
    if lease.pid is not None:
        ctx.process.kill(lease.pid)  # best-effort hygiene; the epoch fence is the guarantee

    # attempt_count includes this lease (its context row was written at mint); the
    # first attempt is not a retry, so retries-so-far is one less.
    retried = ctx.store.attempt_count(lease.chunk_id, lease.node_id) - 1
    if retried < lease.retries_max:
        ctx.store.record_closure(
            lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=reason, closed_at=now
        )
        _requeue(ctx, lease)
    else:
        ctx.store.record_closure(
            lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=_ESCALATED, closed_at=now
        )
        _escalate(ctx, lease)


def _requeue(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Re-attempt the node in the same environments — new session, new lease, fresh epoch (D-082)."""
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    if not bindings:
        _log.warning("requeue with no bound env — cannot re-spawn", chunk_id=lease.chunk_id)
        return
    try:
        envelope = ctx.hub.get_envelope(lease.chunk_id)  # idempotent re-read (D-090)
    except HubClientError:
        return  # the closed attempt is durable; FILL/ADVANCE re-drives next tick
    _log.info("requeuing at node", chunk_id=lease.chunk_id, node=lease.node_name)
    _spawn_attempt(ctx, lease.chunk_id, envelope, _bindings_as_environments(bindings))


def _escalate(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Park the chunk needs-human at the hub, envs held for takeover (D-009/D-083).

    The escalation rides the outbound buffer as an ``escalation.recorded`` fact,
    flushed to the hub's POST /events (D-069), where the fleet derives ``needs_human``
    (an open escalation with no later lease mint — domain/events.md). It carries the
    pasteable takeover command — ``cd <workdir> && <harness resume>`` composed from the
    adapter's session surface (design/harness-adapters.md) — so a human resumes the
    parked session in the agent's own warm worktrees; a requeue's later lease mint
    closes it by supersession (D-067). Environments stay bound throughout.
    """
    now = ctx.clock.now()
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    takeover = ""
    if lease.session_id is not None and bindings:
        takeover = ctx.harness.resume_command(bindings[0].workdir, lease.session_id)
    payload = json.dumps({"chunk_id": lease.chunk_id, "epoch": lease.epoch, "takeover_command": takeover})
    ctx.store.enqueue_outbound(
        kind=ESCALATION_RECORDED, chunk_id=lease.chunk_id, lease_id=lease.lease_id, payload=payload, created_at=now
    )
    _log.info("escalated to needs-human — retries exhausted", chunk_id=lease.chunk_id, takeover=takeover)


def _park_on_ask(ctx: LoopContext, lease: LeaseRecord, ask: AskRecord) -> None:
    """Park the chunk on a question: forward it to the hub and stop the reap clock.

    The worker asked and exited, so there is no live worker to judge or reap ([ask-
    answer.md]): the question rides the outbound buffer up to the hub (store-and-forward,
    D-069), where it becomes the durable row the chunk derives ``waiting_on_human`` from,
    and the local park fact keeps REAP off the dormant lease and ADVANCE from re-parking
    or eliciting a verdict. The env bindings stay held (D-083) so the session is warm for
    the resume. No retry is consumed — a park is not a failed attempt (D-009).
    """
    now = ctx.clock.now()
    payload = json.dumps(
        {
            "question_id": ask.question_id,
            "chunk_id": lease.chunk_id,
            "node_id": lease.node_id,
            "session_id": ask.session_id or lease.session_id,
            "epoch": lease.epoch,
            "question": ask.question,
            "options": ask.options,
            "asked_at": ask.asked_at.isoformat(),
        }
    )
    ctx.store.enqueue_outbound(
        kind=QUESTION_ASKED, chunk_id=lease.chunk_id, lease_id=lease.lease_id, payload=payload, created_at=now
    )
    ctx.store.record_park(lease_id=lease.lease_id, chunk_id=lease.chunk_id, question_id=ask.question_id, parked_at=now)
    _log.info("chunk parked on question", chunk_id=lease.chunk_id, question_id=ask.question_id)


def _resume_if_answered(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Poll a parked lease's question; on an answer, resume the dormant session (D-050).

    The answer is a durable row at the hub, so this is crash-safe and re-runnable: while
    the question is unanswered the poll is a no-op and the reap clock stays stopped. Once
    answered, the agent is **reconstituted around the answer** — the same session, same
    lease, same node-step (D-082) — via the adapter's resume-with-message. The lease's
    new pid is recorded so it reads live again, the park is closed, and ``answer.delivered``
    is buffered up to the hub (board detail; the status already flipped at question.answered).
    """
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

    # The resume prompt reconstitutes the agent around the answer ([ask-answer.md]). The
    # human framing rides a leading comment line and the answer itself is the payload, so
    # the agent reads "who answered" as context and acts on the answer body — a shape the
    # blizzard-mock façade (prompt-is-program) executes directly, and a real harness reads
    # as ordinary resume text (the exact prose is unpinned, D-061).
    who = question.answered_by or "operator"
    message = f"# Answer from {who}. Continue.\n{question.answer}"
    pid = ctx.harness.resume_with_message(bindings[0].workdir, lease.session_id or "", message)
    now = ctx.clock.now()
    # The resumed worker runs under the same lease and session; record its new pid so the
    # lease reads live again (REAP/ADVANCE treat it as any running worker from here).
    ctx.store.record_spawn(
        lease.lease_id,
        pid=pid,
        process_start_time=ctx.process.start_time(pid) or "",
        session_id=lease.session_id or "",
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


def _collect_asset_artifacts(
    envelope: NodeEnvelope, git_artifacts: list[SubmittedArtifact], assessment: str
) -> list[SubmittedArtifact]:
    """Emit an asset artifact per produced name no git commit covers (D-026).

    The engine has no file convention for assets (D-056): a node that declares it
    ``produces`` a name — the review node's ``findings`` — but pushes no git commit of
    that name emits the worker's judgement assessment as the asset's content. Git-commit
    artifacts are named by repo, so a build node producing repo commits yields no
    assets; a read-only review node yields its findings. Content may be empty (a clean
    pass) — the asset still lands, and only a fail routes it back into build (latest-by-epoch)."""
    from blizzard.hub.domain.artifacts import ArtifactKind

    covered = {a.name for a in git_artifacts}
    return [
        SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content=assessment)
        for name in envelope.node.produces
        if name not in covered
    ]


def _push_and_collect_artifacts(ctx: LoopContext, bindings: list[EnvBindingRecord]) -> list[SubmittedArtifact]:
    """Discover the produced git commits, push their branches, and name them (D-026)."""
    from blizzard.hub.domain.artifacts import ArtifactKind

    submitted: list[SubmittedArtifact] = []
    for binding in bindings:
        for produced in ctx.worktree_git.find_produced_artifacts(binding.workdir, ctx.config.base_branch):
            ctx.worktree_git.push(produced.repo_workdir, produced.branch_name)
            submitted.append(
                SubmittedArtifact(
                    name=produced.repo,
                    kind=ArtifactKind.GIT_COMMIT,
                    repo=produced.repo,
                    branch_name=produced.branch_name,
                    commit_hash=produced.commit_hash,
                )
            )
    return submitted


def _release_all(ctx: LoopContext, chunk_id: str) -> None:
    """Release every held environment at the chunk's tenure end (D-083)."""
    now = ctx.clock.now()
    for binding in ctx.store.bindings_for_chunk(chunk_id):
        ctx.provider.release(binding.environment_id)
        ctx.store.record_release(chunk_id=chunk_id, environment_id=binding.environment_id, released_at=now)


def _release_acquired(ctx: LoopContext, acquired: list[AcquiredEnvironment]) -> None:
    """Release just-acquired (unbound) environments after a lost claim (D-080)."""
    for a in acquired:
        ctx.provider.release(a.environment_id)


def _bindings_as_environments(bindings: list[EnvBindingRecord]) -> list[AcquiredEnvironment]:
    return [AcquiredEnvironment(environment_id=b.environment_id, workdir=b.workdir) for b in bindings]


def _elicitation_tail(envelope: NodeEnvelope) -> str:
    """The engine-generated ``<Choice>`` elicitation appended to the judgement prompt (D-042).

    Emitted as ``#``-prefixed lines so the tail is harness-agnostic: inert whether
    the judgement prompt is LLM prose (a comment block a real coding harness still
    reads) or a mock behavior *script* (the mock ``exec``s the prompt, and a bare
    prose tail would be a ``SyntaxError``).
    """
    lines = ["", "", "# Select exactly one outcome and reply with <Choice>name</Choice>:"]
    for choice in envelope.node.choices:
        lines.append(f"#   - {choice.name}: {choice.description}")
    return "\n".join(lines)
