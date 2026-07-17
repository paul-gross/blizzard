"""The hub store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``): every table here records a
thing that definitely happened at a definite time; no ``status`` column exists,
and the derivations over these rows live in :mod:`blizzard.hub.domain.work`.
Timestamps are stamped by application code from the injected clock, never a
``server_default=func.now()`` (``bzh:injected-clock``). Portable-SQL surface only
(``bzh:sql-portable``): the same DDL runs on sqlite and postgres.

The walking-skeleton tables (P6) carry ONE chunk ingest -> claim -> commit ->
deliver -> land end to end. Tables the thin slice does not yet exercise
(``chunk_stopped``, ``escalations``) are present because the status-derivation
*precedence* is only correct with them — a seam shaped, not dead weight. The
``questions``/``question_answers`` tables land the ask/answer rendezvous (MVP
criterion 7); the gate tables (``decisions``, ``decision_resolutions``, ``requeues``)
land the human-gate loop (MVP criterion 12) and feed the ``waiting_on_human``
derivation; ``transitions.decision_id`` (carried since P6) is what the resolving
transition points back at.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from blizzard.foundation.store.utc import UtcDateTime

metadata = MetaData()

# --- Workflow graphs (immutable definitions, reified) -------------------------

graphs = Table(
    "graphs",
    metadata,
    Column("graph_id", String, primary_key=True),  # gr_<ulid>
    Column("name", String, nullable=False),
    Column("entry_node_id", String, nullable=False),
    Column("definition_yaml", Text, nullable=False),  # the inlined source, for audit/re-export
    Column("created_at", UtcDateTime, nullable=False),
)

graph_nodes = Table(
    "graph_nodes",
    metadata,
    Column("node_id", String, primary_key=True),  # nd_<ulid>
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("name", String, nullable=False),
    Column("executor", String, nullable=False),  # runner | hub
    Column("prompt", Text, nullable=True),  # inlined text, never a path
    Column("judgement_prompt", Text, nullable=True),  # the verdict-elicitation prompt; null at a gate/hub node
    Column("session", String, nullable=False),  # resume | fresh
    Column("judged_by", String, nullable=False),  # worker | human
    Column("retries_max", Integer, nullable=True),
    Column("retries_exhausted", String, nullable=True),  # escalate
    Column("mode", String, nullable=True),  # deliver hub node: merge-to-main | open-pr
    Column("produces", Text, nullable=True),  # JSON list of artifact names; e.g. review's `review-findings`
    Column("checks", Text, nullable=True),  # JSON list of check commands, worker-run in-session
)

graph_choices = Table(
    "graph_choices",
    metadata,
    Column("choice_id", String, primary_key=True),  # cho_<ulid>
    Column("node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
    Column("name", String, nullable=False),
    Column("description", Text, nullable=False),
)

graph_edges = Table(
    "graph_edges",
    metadata,
    Column("edge_id", String, primary_key=True),
    Column("from_node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
    Column("choice_id", String, ForeignKey("graph_choices.choice_id"), nullable=False),
    Column("to_node_name", String, nullable=False),  # a node name, or the reserved 'done'
    Column("prompt_addendum", Text, nullable=True),  # inlined arrival context
)

# --- Chunks and their PM pointers (chunk.minted) ------------------------------

chunks = Table(
    "chunks",
    metadata,
    Column("chunk_id", String, primary_key=True),  # ch_<ulid>
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),  # pinned at mint
    Column("minted_at", UtcDateTime, nullable=False),
)

chunk_pm_pointers = Table(
    "chunk_pm_pointers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("source", String, nullable=False),
    Column("ref", String, nullable=False),
)

# --- Movement record (transition.recorded) ------------------------------------

transitions = Table(
    "transitions",
    metadata,
    Column("transition_id", String, primary_key=True),  # tr_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("from_node_id", String, nullable=True),  # null on the first transition out of entry
    Column("to_node_id", String, nullable=False),  # a node_id, or 'done' terminal
    Column("choice_name", String, nullable=True),  # the judgement's selected choice
    Column("decision_id", String, nullable=True),  # gates only; shaped for P7
    Column("epoch", Integer, nullable=False),  # the fencing epoch checked against latest
    Column("runner_id", String, nullable=False),  # reporting author, or the hub coordinator
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Artifacts (the chunk artifact store) --------------------------------------

artifacts = Table(
    "artifacts",
    metadata,
    Column("artifact_id", String, primary_key=True),  # art_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),  # exact provenance
    Column("node_name", String, nullable=False),  # the {node} store-key component (name, not id)
    Column("epoch", Integer, nullable=False),
    Column("name", String, nullable=False),  # the {artifact-name} store-key component
    Column("kind", String, nullable=False),  # git_commit | asset
    Column("data", Text, nullable=False),  # '<branch>:<commit>' | raw content
    Column("repo", String, nullable=True),  # git_commit only
    Column("produced_at", UtcDateTime, nullable=False),
)

# --- Lease facts (lease.minted, runner-reported) -------------------------------

lease_facts = Table(
    "lease_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # the fence input the transition check consumes
    Column("runner_id", String, nullable=False),
    Column("minted_at", UtcDateTime, nullable=False),
)

# --- Routes (route.created / route.released) ----------------------------------

route_created = Table(
    "route_created",
    metadata,
    Column("route_id", String, primary_key=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("runner_id", String, nullable=False),
    Column("workspace_id", String, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    # The monotonic route-event tiebreak (see work.newest_live_route) — a
    # per-chunk counter shared with route_released.seq, assigned in real write order.
    Column("seq", Integer, nullable=False),
)

route_environments = Table(
    "route_environments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("route_id", String, ForeignKey("route_created.route_id"), nullable=False),
    Column("environment_id", String, nullable=False),  # opaque
)

route_released = Table(
    "route_released",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("released_at", UtcDateTime, nullable=False),
    # See route_created.seq — the same per-chunk counter, so a created/released pair
    # tied on timestamp is still totally ordered by real write order.
    Column("seq", Integer, nullable=False),
)

# --- Delivery landing facts (per-repo, then whole-chunk) ----------------------

delivery_repo_landed = Table(
    "delivery_repo_landed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("repo", String, nullable=False),
    Column("commit_hash", String, nullable=False),
    Column("landed_at", UtcDateTime, nullable=False),
)

delivery_landed = Table(
    "delivery_landed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("landed_at", UtcDateTime, nullable=False),  # terminal: all repos landed
)

# --- Open-PR delivery facts (pr.opened / pr.closed) ---------------------------
#
# The ``open-pr`` deliver mode: instead of merging, the coordinator opens a PR
# per repo and PARKS the chunk — it records ``pr.opened`` here but writes NO terminal
# transition and NO ``route_released``, so the chunk derives ``delivering`` (awaiting an
# external merge) with its environments held. A later poll or the on-demand
# ``POST /chunks/{id}/check-delivery`` route detects the PR's terminal state and
# records ``pr.closed`` — the terminal fact that flips the chunk to ``done`` (either
# disposition), carrying ``merged`` and the actually-landed commit where one exists.

delivery_pr_opened = Table(
    "delivery_pr_opened",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("repo", String, nullable=False),  # the forge repo coordinate the PR was opened on
    Column("pr_number", Integer, nullable=False),
    Column("pr_url", String, nullable=False),  # the PR's html url — surfaced on the board
    Column("commit_hash", String, nullable=False),  # the authoritative head the PR carries
    Column("opened_at", UtcDateTime, nullable=False),
    # A ``pr.opened`` write is idempotent per (chunk, repo) (20260716_2206_hub_pr_opened_idempotent):
    # the coordinator's deliver node runs on both a fresh apply and an idempotent replay,
    # and its DB-backed ``open_prs`` skip-set (a store read each call, not an
    # in-memory cache) has a narrow race between that read and the write. This constraint
    # is the actual close of that race — the store adapter's ``record_pr_opened`` treats a
    # collision as a harmless duplicate write, not an error.
    UniqueConstraint("chunk_id", "repo", name="uq_delivery_pr_opened_chunk_repo"),
)

delivery_pr_closed = Table(
    "delivery_pr_closed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("repo", String, nullable=False),
    Column("pr_number", Integer, nullable=False),
    Column("merged", Boolean, nullable=False),  # merged vs closed-without-merge — both terminal
    Column("landed_commit", String, nullable=True),  # the merge commit where one exists
    Column("closed_at", UtcDateTime, nullable=False),
)

# --- Readiness: the not-ready resting state and its promotion --------
#
# Ingest mints a chunk in a NOT-READY resting state: visible on the board but
# never claimed by a runner. A ``chunk.promoted`` fact — appended by ``POST
# /chunks/{id}/promote`` — flips it to ``ready`` (facts append, status derives). An
# un-promoted chunk with no ``chunk_promoted`` row derives ``not_ready`` and so is
# excluded from ``list_ready``/``/queue/peek``; existing chunks predating this table
# have no row and are back-filled by the migration so they stay claimable.

chunk_promoted = Table(
    "chunk_promoted",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("promoted_at", UtcDateTime, nullable=False),  # not_ready -> ready
)

# --- Facts that make the derivation precedence correct (shaped) -------------

chunk_stopped = Table(
    "chunk_stopped",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("stopped_at", UtcDateTime, nullable=False),  # terminal operator abandonment
)

escalations = Table(
    "escalations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # closed by a later lease mint, not a resolution
    Column("takeover_command", Text, nullable=False, server_default=""),  # the pasteable resume command
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Questions and answers (the ask/answer rendezvous) ----------------------
#
# A worker facing an undecidable choice runs ``blizzard runner ask`` and exits; the
# runner forwards the question here, where it becomes a durable row (question.asked).
# Open/answered is derived: a question is open exactly while no answer row
# exists. The answer is first-write-wins CAS — the ``question_answers`` primary key
# IS the question id, so the second concurrent writer's insert fails and the loser is
# told the winning answer.

questions = Table(
    "questions",
    metadata,
    Column("question_id", String, primary_key=True),  # qn_<ulid> (runner-minted)
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the parked chunk
    Column("node_id", String, nullable=True),  # the node the worker parked at
    Column("session_id", String, nullable=True),  # the dormant session to resume around the answer
    Column("runner_id", String, nullable=False),  # the runner holding the session
    Column("epoch", Integer, nullable=False),  # the parked lease's fencing epoch
    Column("question", Text, nullable=False),
    Column("options", Text, nullable=False),  # JSON list[str] of offered choices (may be empty)
    Column("asked_at", UtcDateTime, nullable=False),  # reap clock stops for the chunk from here
)

question_answers = Table(
    "question_answers",
    metadata,
    # The primary key IS the question id: the CAS that makes answers first-write-wins —
    # a racing second insert collides and the loser reads back the winning row.
    Column("question_id", String, ForeignKey("questions.question_id"), primary_key=True),
    Column("answer", Text, nullable=False),  # the chosen option or free text, carried into the resume prompt
    Column("answered_by", String, nullable=False),  # who won the CAS
    Column("answered_at", UtcDateTime, nullable=False),
)

answer_deliveries = Table(
    "answer_deliveries",
    metadata,
    # answer.delivered (runner-minted): the resume-with-answer executed. Board detail
    # only — the chunk's status already flipped to running at question.answered.
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("question_id", String, ForeignKey("questions.question_id"), nullable=False),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("delivered_at", UtcDateTime, nullable=False),
)

# --- Human gates: decisions and their resolutions -------------
#
# A gate parks a chunk on an open Decision — a durable multiple-choice row a person
# resolves. The decision is written either by the hub when a
# transition lands on a human-judged node (a *graph* gate) or by the runner in place
# of a transition for a node it was configured to gate (a *runner-config* gate).
# Resolved-ness is DERIVED (bzh:facts-not-status): a decision with a row in
# ``decision_resolutions`` is resolved; the resolving Transition the holding runner
# records later carries the same ``decision_id`` (transitions.decision_id).

decisions = Table(
    "decisions",
    metadata,
    Column("decision_id", String, primary_key=True),  # dec_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),  # the gate node awaiting the decision
    Column("node_name", String, nullable=False),  # the node's name — what runner gate-config matches
    Column("epoch", Integer, nullable=False),  # the parked step's fence; stale decisions rejected
    Column("choices", Text, nullable=False),  # JSON list of {name, description} — the buttons
    Column("submitted_at", UtcDateTime, nullable=False),
)

decision_resolutions = Table(
    "decision_resolutions",
    metadata,
    # decision_id is the PK — the first write wins the CAS; a second resolution is
    # rejected and told who already resolved (like an answer).
    Column("decision_id", String, ForeignKey("decisions.decision_id"), primary_key=True),
    Column("choice", String, nullable=False),  # the picked choice name — routes the resolving transition
    Column("resolved_by", String, nullable=False),
    Column("resolved_at", UtcDateTime, nullable=False),
)

# --- Requeue facts (close needs_human by supersession) ------------------------
#
# ``blizzard hub requeue <chunk>`` appends this fact to close an open escalation by
# supersession (never a resolution fact): an escalation stays open only while no later
# lease mint AND no later requeue supersedes it. The
# requeue also releases the route so the chunk re-derives ``ready`` and re-enters FILL
# at its current node — a fresh attempt.

requeues = Table(
    "requeues",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("requeued_at", UtcDateTime, nullable=False),  # supersedes an earlier escalation
)

# --- Chunk pause facts (chunk.paused / chunk.resumed — issue #46) -----------
#
# An operator-level brake over one specific chunk, orthogonal to the runner's own brake
# (``runner_pause_facts`` above) and to detach (which gives up the claim). Append-only,
# newest-fact-wins, mirroring ``runner_pause_facts`` exactly.

chunk_pause_facts = Table(
    "chunk_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("paused", Boolean, nullable=False),  # paused derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),  # who flipped it — recorded on the fact
)

# --- Store-and-forward high-water mark (per-runner idempotency) ---------------
#
# The hub's dedup memory for the runner→hub fact push (POST /events): the greatest
# per-runner sequence number it has already applied. A pushed fact with seq ≤ mark
# is already-applied and re-acked without re-applying — the idempotent replay after
# a lost ack or an outage backlog drain. One row per runner, advanced monotonically.

runner_high_water = Table(
    "runner_high_water",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("seq", Integer, nullable=False),  # greatest applied per-runner seq
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Queue shaping: ready-queue ordering ----------------------
#
# Ready-queue ordering is an explicit hub-side property (the board's Prioritize
# control): the operator moves a ready chunk to a position, and GET /queue/peek
# honours it. Facts append, order derives: each reorder appends ONE row —
# the moved chunk's new float ``position``, computed between its target neighbours —
# and a chunk's effective position is its newest such fact, or its ``minted_at``
# instant (as a unix timestamp) before it was ever moved. Ready chunks sort ascending
# by effective position, so "move to top" is simply a position below the current least.

queue_positions = Table(
    "queue_positions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("position", Float, nullable=False),  # lower sorts earlier; newest fact per chunk wins
    Column("set_at", UtcDateTime, nullable=False),
)

# --- Queue shaping: grouping (chunk.grouped) -----------------------------------
#
# Group N unacquired (ready) chunks into one surviving chunk: the survivor absorbs the
# union of their PM pointers (pointers become plural, appended to chunk_pm_pointers),
# and each merged-away chunk records a ``chunk.grouped`` fact naming the survivor. A
# grouped chunk is EPHEMERAL: it is removed from every listing rather than
# deriving a status, exactly like a discard — the PM item lives on
# as a pointer on the survivor.

chunk_grouped = Table(
    "chunk_grouped",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the merged-away chunk
    Column("grouped_into", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the survivor
    Column("grouped_at", UtcDateTime, nullable=False),
)

# --- The fleet registry (runner.registered / paused / resumed) ----------------
#
# Runners register on startup (runner_id + workspace_id) and appear on the board. The
# registration row is an upsert: ``last_seen_at`` is a refreshed timestamp (not a fact),
# bumped by the register call and the dedicated heartbeat; liveness derives from
# it against a staleness threshold (never a stored column). Operational state is
# declarative and append-only: pause/resume facts land in ``runner_pause_facts`` and
# ``paused`` derives from the newest one — the runner reads it
# back on its outbound pull and adheres (paused = no new claims; in-flight runs on).

runner_registrations = Table(
    "runner_registrations",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("workspace_id", String, nullable=False),  # the per-runner workspace binding
    Column("registered_at", UtcDateTime, nullable=False),
    Column("last_seen_at", UtcDateTime, nullable=False),  # liveness derives from this
)

runner_pause_facts = Table(
    "runner_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
    Column("paused", Boolean, nullable=False),  # paused derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),  # who flipped it — recorded on the fact
)

# The runner's *own* brake, as reported to us (issue #43). A separate table from
# ``runner_pause_facts`` above because they are separate concepts with separate authors:
# that one is the fleet's brake, authored here and pulled down by the runner; this one is
# authored on the runner machine and arrives through its outbound buffer, so the
# hub is a reader of it and never sets it. Keeping them apart is what lets the board say
# *which* brake is on — the runner declining, the fleet coercing, or both.
#
# No ForeignKey to ``runner_registrations``: a fact can arrive from a runner the registry
# has not seen yet (the buffer replays an outage in FIFO order, and its pause may precede
# its registration). ``_apply`` decides what to do with an unknown runner; the schema does
# not make the arrival unrepresentable.

runner_local_pause_facts = Table(
    "runner_local_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, nullable=False),
    Column("paused", Boolean, nullable=False),  # locally_paused derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),  # the runner's clock, off the fact's payload
    Column("set_by", String, nullable=False),
)
