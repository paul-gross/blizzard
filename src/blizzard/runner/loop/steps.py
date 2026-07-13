"""The reconciliation step functions — REAP → PULL → FILL → ADVANCE (``bzh:steppable-loop``).

Each is an individually callable function of a :class:`LoopContext`; the tick driver
and the ``blizzard runner tick`` CLI verb call them in order. Every step is
idempotent and holds no state of its own — all facts live in the runner store, so a
crash mid-tick followed by a restart re-runs the tick harmlessly (D-023/D-028), and
startup recovery is just REAP running first.

P6 walking-skeleton semantics for the dead-worker split (design/runner/loop.md):
a **session-bearing** dead worker is a *done declaration* (exit-is-done, D-055) and
belongs to ADVANCE — its judgement reply, or its absence (D-009), tells a done from
a crash. REAP handles only the residue ADVANCE structurally cannot judge: a lease
whose worker never reached spawn-return (no pid/session — killed mid-FILL). Both the
verdict-less-exit failure (ADVANCE) and the orphan (REAP) route through one
``requeue-or-escalate`` decision keyed on the node's retry budget (D-078/D-009).
Heartbeat-based stall detection is P7; in P6 liveness is (pid, start_time) alone.
"""

from __future__ import annotations

import json
import uuid

from blizzard.foundation.ids import LEASE_PREFIX, mint
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.environments.provider import AcquiredEnvironment, WorkspaceAcquisitionError
from blizzard.runner.harness.adapter import WorkerPreamble
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.hub import HubClientError
from blizzard.runner.store.repository import EnvBindingRecord, LeaseRecord, NewLease
from blizzard.wire.completion import CompletionSubmission, SubmittedArtifact
from blizzard.wire.envelope import ApplyOutcome, NodeEnvelope
from blizzard.wire.route import RouteClaim

_log = get_logger("blizzard.runner.loop")

# Closure reasons (lease_closures.reason).
_TRANSITIONED = "transitioned"
_REAPED = "reaped"
_FAILED = "failed"
_ESCALATED = "escalated"

# The env count a solo chunk wants; batching (K>1) is parked (design/runner/environments.md).
_SOLO_ENV_COUNT = 1


# --------------------------------------------------------------------------- #
# REAP
# --------------------------------------------------------------------------- #


def reap(ctx: LoopContext) -> None:
    """Expire leases whose worker is gone and that ADVANCE cannot judge.

    In P6 that is exactly the lease with no recorded pid: minted at FILL but never
    spawned (a crash in the mint→spawn window). A session-bearing dead worker is a
    done declaration ADVANCE owns; a session-bearing *live* worker is left running.
    An orphan is a failed execution attempt (D-078) — requeue or escalate.
    """
    for lease in ctx.store.list_active_leases():
        if lease.pid is None or lease.session_id is None:
            _log.info("reaping unspawned lease", lease_id=lease.lease_id, chunk_id=lease.chunk_id)
            _fail_attempt(ctx, lease, reason=_REAPED)
            continue
        # A recorded worker that has died is ADVANCE's (exit-is-done); a live one runs on.
        # REAP's best-effort kill of a still-alive reaped worker is P7 (stall detection).


# --------------------------------------------------------------------------- #
# PULL
# --------------------------------------------------------------------------- #


def pull(ctx: LoopContext) -> None:
    """Exchange facts with the hub (outbound-only, D-012).

    **P6 thin slice.** The buffered-fact flush (escalations, transitions) and the
    ``paused`` adherence are the store-and-forward channel (D-069) that lands in P7;
    the hub exposes no fact-ingest route yet, so PULL leaves the durable buffer in
    place. The seam is kept so the flusher bolts on without reshaping the tick.
    """
    pending = ctx.store.pending_outbound()
    if pending:
        _log.debug("outbound facts buffered (P6: flushed in P7)", count=len(pending))


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
    push its artifacts, and submit the epoch-fenced completion; (b) a chunk held at a
    hub node (envs bound, no active lease) is polled for the hub's terminal outcome —
    a landed delivery releases its environments (D-066).
    """
    for lease in ctx.store.list_active_leases():
        if lease.pid is None or lease.session_id is None:
            continue  # REAP's residue
        if ctx.process.is_alive(lease.pid, lease.process_start_time or ""):
            continue  # worker still running
        _advance_exited_worker(ctx, lease)

    for chunk_id in ctx.store.live_tenure_chunk_ids():
        if ctx.store.active_lease_for_chunk(chunk_id) is None:
            _poll_hub_node(ctx, chunk_id)


def _advance_exited_worker(ctx: LoopContext, lease: LeaseRecord) -> None:
    """Elicit the verdict, push artifacts, and submit the node-step's completion."""
    if lease.session_id is None:
        return  # not spawned — REAP's residue (guarded by the caller too)
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

    # 3. Submit the completion — one atomic, epoch-fenced write (D-036).
    submission = CompletionSubmission(
        choice=choice,
        epoch=lease.epoch,
        runner_id=ctx.config.runner_id,
        from_node_id=lease.node_id,
        check_results=[],  # in-session check assessment is P7; the model carries them (D-077)
        artifacts=artifacts,
    )
    try:
        response = ctx.hub.submit_completion(lease.chunk_id, submission)
    except HubClientError:
        return  # completion durable in the store; the worker mid-node is unaffected

    if response.outcome == ApplyOutcome.FAILURE:
        _log.warning("completion rejected", chunk_id=lease.chunk_id, detail=response.detail or "")
        _fail_attempt(ctx, lease, reason=_FAILED)
        return

    now = ctx.clock.now()
    ctx.store.record_closure(
        lease_id=lease.lease_id, chunk_id=lease.chunk_id, node_id=lease.node_id, reason=_TRANSITIONED, closed_at=now
    )
    _apply_response(ctx, lease, response.outcome, response.next_envelope, bindings)


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
    handle = ctx.harness.spawn(envelope, WorkerPreamble(environments=environments), session_hint=str(uuid.uuid4()))
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
    """Park the chunk needs-human, envs held for takeover (D-083); buffer the fact (D-069).

    **P6 thin slice.** The hub exposes no needs-human route yet, so the escalation is
    recorded as a durable local buffer fact (flushed to the hub in P7's PULL) and the
    chunk derives ``needs_human`` from it locally. Environments stay bound — takeover
    lands in the agent's own worktrees.
    """
    now = ctx.clock.now()
    bindings = ctx.store.bindings_for_chunk(lease.chunk_id)
    takeover = ""
    if lease.session_id is not None and bindings:
        takeover = ctx.harness.resume_command(bindings[0].workdir, lease.session_id)
    payload = json.dumps(
        {
            "chunk_id": lease.chunk_id,
            "node_id": lease.node_id,
            "epoch": lease.epoch,
            "runner_id": lease.runner_id,
            "takeover_command": takeover,
        }
    )
    ctx.store.enqueue_outbound(kind="escalation.needs_human", chunk_id=lease.chunk_id, payload=payload, created_at=now)
    _log.info("escalated to needs-human — retries exhausted", chunk_id=lease.chunk_id, takeover=takeover)


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
    """The engine-generated ``<Choice>`` elicitation appended to the judgement prose (D-042)."""
    lines = ["", "", "Select exactly one outcome and reply with `<Choice>name</Choice>`:"]
    for choice in envelope.node.choices:
        lines.append(f"- `{choice.name}`: {choice.description}")
    return "\n".join(lines)
