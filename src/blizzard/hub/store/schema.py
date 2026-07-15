"""The hub store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``): every table here records a
thing that definitely happened at a definite time; no ``status`` column exists,
and the derivations over these rows live in :mod:`blizzard.hub.domain.work` (D-004).
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
transition points back at (D-045).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

# --- Workflow graphs (immutable definitions, reified — D-033/D-071) ---------

graphs = Table(
    "graphs",
    metadata,
    Column("graph_id", String, primary_key=True),  # gr_<ulid>
    Column("name", String, nullable=False),
    Column("entry_node_id", String, nullable=False),
    Column("definition_yaml", Text, nullable=False),  # the inlined source, for audit/re-export
    Column("created_at", DateTime, nullable=False),
)

graph_nodes = Table(
    "graph_nodes",
    metadata,
    Column("node_id", String, primary_key=True),  # nd_<ulid>
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("name", String, nullable=False),
    Column("executor", String, nullable=False),  # runner | hub
    Column("prompt", Text, nullable=True),  # inlined text, never a path (D-033)
    Column("judgement_prompt", Text, nullable=True),  # the verdict-elicitation prompt (D-038); null at a gate/hub node
    Column("session", String, nullable=False),  # resume | fresh
    Column("judged_by", String, nullable=False),  # worker | human
    Column("retries_max", Integer, nullable=True),
    Column("retries_exhausted", String, nullable=True),  # escalate
    Column("mode", String, nullable=True),  # deliver hub node: merge-to-main | open-pr
    Column("produces", Text, nullable=True),  # JSON list of artifact names (D-026); e.g. review's `review-findings`
    Column("checks", Text, nullable=True),  # JSON list of check commands (D-077), worker-run in-session
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
    Column("prompt_addendum", Text, nullable=True),  # inlined arrival context (D-038)
)

# --- Chunks and their PM pointers (chunk.minted — D-024/D-047) --------------

chunks = Table(
    "chunks",
    metadata,
    Column("chunk_id", String, primary_key=True),  # ch_<ulid> (D-075)
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),  # pinned at mint
    Column("minted_at", DateTime, nullable=False),
)

chunk_pm_pointers = Table(
    "chunk_pm_pointers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("provider", String, nullable=False),
    Column("url", String, nullable=False),
)

# --- Movement record (transition.recorded — D-027/D-036) --------------------

transitions = Table(
    "transitions",
    metadata,
    Column("transition_id", String, primary_key=True),  # tr_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("from_node_id", String, nullable=True),  # null on the first transition out of entry
    Column("to_node_id", String, nullable=False),  # a node_id, or 'done' terminal
    Column("choice_name", String, nullable=True),  # the judgement's selected choice
    Column("decision_id", String, nullable=True),  # gates only (D-045); shaped for P7
    Column("epoch", Integer, nullable=False),  # the fencing epoch checked against latest (D-007)
    Column("runner_id", String, nullable=False),  # reporting author, or the hub coordinator (D-079)
    Column("recorded_at", DateTime, nullable=False),
)

# --- Artifacts (the chunk artifact store — D-036) ---------------------------

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
    Column("data", Text, nullable=False),  # '<branch>:<commit>' | raw content (D-036)
    Column("repo", String, nullable=True),  # git_commit only
    Column("produced_at", DateTime, nullable=False),
)

# --- Lease facts (lease.minted, runner-reported — D-044) --------------------

lease_facts = Table(
    "lease_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # the fence input the transition check consumes (D-007)
    Column("runner_id", String, nullable=False),
    Column("minted_at", DateTime, nullable=False),
)

# --- Routes (route.created / route.released — D-021/D-080/D-088) ------------

route_created = Table(
    "route_created",
    metadata,
    Column("route_id", String, primary_key=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("runner_id", String, nullable=False),
    Column("workspace_id", String, nullable=False),
    Column("created_at", DateTime, nullable=False),
)

route_environments = Table(
    "route_environments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("route_id", String, ForeignKey("route_created.route_id"), nullable=False),
    Column("environment_id", String, nullable=False),  # opaque (D-021)
)

route_released = Table(
    "route_released",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("released_at", DateTime, nullable=False),
)

# --- Delivery landing facts (per-repo, then whole-chunk — D-030/D-091) ------

delivery_repo_landed = Table(
    "delivery_repo_landed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("repo", String, nullable=False),
    Column("commit_hash", String, nullable=False),
    Column("landed_at", DateTime, nullable=False),
)

delivery_landed = Table(
    "delivery_landed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("landed_at", DateTime, nullable=False),  # terminal: all repos landed (D-030)
)

# --- Open-PR delivery facts (pr.opened / pr.closed — D-059/D-065) -----------
#
# The ``open-pr`` deliver mode (D-059): instead of merging, the coordinator opens a PR
# per repo and PARKS the chunk — it records ``pr.opened`` here but writes NO terminal
# transition and NO ``route_released``, so the chunk derives ``delivering`` (awaiting an
# external merge) with its environments held (D-066). A later poll or the on-demand
# ``POST /chunks/{id}/check-delivery`` route detects the PR's terminal state (D-065) and
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
    Column("commit_hash", String, nullable=False),  # the authoritative head the PR carries (D-060)
    Column("opened_at", DateTime, nullable=False),
)

delivery_pr_closed = Table(
    "delivery_pr_closed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("repo", String, nullable=False),
    Column("pr_number", Integer, nullable=False),
    Column("merged", Boolean, nullable=False),  # merged vs closed-without-merge — both terminal (D-065)
    Column("landed_commit", String, nullable=True),  # the merge commit where one exists
    Column("closed_at", DateTime, nullable=False),
)

# --- Readiness: the not-ready resting state and its promotion (D-004) --------
#
# Ingest mints a chunk in a NOT-READY resting state (D-103): visible on the board but
# never claimed by a runner. A ``chunk.promoted`` fact — appended by ``POST
# /chunks/{id}/promote`` — flips it to ``ready`` (facts append, status derives). An
# un-promoted chunk with no ``chunk_promoted`` row derives ``not_ready`` and so is
# excluded from ``list_ready``/``/queue/peek``; existing chunks predating this table
# have no row and are back-filled by the migration so they stay claimable (D-103).

chunk_promoted = Table(
    "chunk_promoted",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("promoted_at", DateTime, nullable=False),  # not_ready -> ready (D-103)
)

# --- Facts that make the derivation precedence correct (shaped) -------------

chunk_stopped = Table(
    "chunk_stopped",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("stopped_at", DateTime, nullable=False),  # terminal operator abandonment (D-067)
)

escalations = Table(
    "escalations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # closed by a later lease mint, not a resolution (D-067)
    Column("takeover_command", Text, nullable=False, server_default=""),  # the pasteable resume command (D-035)
    Column("recorded_at", DateTime, nullable=False),
)

# --- Questions and answers (the ask/answer rendezvous — questions.md) --------
#
# A worker facing an undecidable choice runs ``blizzard runner ask`` and exits; the
# runner forwards the question here, where it becomes a durable row (question.asked).
# Open/answered is derived (D-004): a question is open exactly while no answer row
# exists. The answer is first-write-wins CAS — the ``question_answers`` primary key
# IS the question id, so the second concurrent writer's insert fails and the loser is
# told the winning answer ([ask-answer.md]).

questions = Table(
    "questions",
    metadata,
    Column("question_id", String, primary_key=True),  # qn_<ulid> (runner-minted, D-075)
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the parked chunk
    Column("node_id", String, nullable=True),  # the node the worker parked at
    Column("session_id", String, nullable=True),  # the dormant session to resume around the answer
    Column("runner_id", String, nullable=False),  # the runner holding the session
    Column("epoch", Integer, nullable=False),  # the parked lease's fencing epoch (D-007)
    Column("question", Text, nullable=False),
    Column("options", Text, nullable=False),  # JSON list[str] of offered choices (may be empty)
    Column("asked_at", DateTime, nullable=False),  # reap clock stops for the chunk from here
)

question_answers = Table(
    "question_answers",
    metadata,
    # The primary key IS the question id: the CAS that makes answers first-write-wins —
    # a racing second insert collides and the loser reads back the winning row.
    Column("question_id", String, ForeignKey("questions.question_id"), primary_key=True),
    Column("answer", Text, nullable=False),  # the chosen option or free text, carried into the resume prompt
    Column("answered_by", String, nullable=False),  # who won the CAS
    Column("answered_at", DateTime, nullable=False),
)

answer_deliveries = Table(
    "answer_deliveries",
    metadata,
    # answer.delivered (runner-minted): the resume-with-answer executed. Board detail
    # only — the chunk's status already flipped to running at question.answered.
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("question_id", String, ForeignKey("questions.question_id"), nullable=False),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("delivered_at", DateTime, nullable=False),
)

# --- Human gates: decisions and their resolutions (D-045/D-032) -------------
#
# A gate parks a chunk on an open Decision — a durable multiple-choice row a person
# resolves (design/domain/work.md). The decision is written either by the hub when a
# transition lands on a human-judged node (a *graph* gate) or by the runner in place
# of a transition for a node it was configured to gate (a *runner-config* gate,
# D-032). Resolved-ness is DERIVED (bzh:facts-not-status): a decision with a row in
# ``decision_resolutions`` is resolved; the resolving Transition the holding runner
# records later carries the same ``decision_id`` (transitions.decision_id, D-027).

decisions = Table(
    "decisions",
    metadata,
    Column("decision_id", String, primary_key=True),  # dec_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),  # the gate node awaiting the decision
    Column("node_name", String, nullable=False),  # the node's name — what runner gate-config matches (D-041)
    Column("epoch", Integer, nullable=False),  # the parked step's fence; stale decisions rejected (D-007)
    Column("choices", Text, nullable=False),  # JSON list of {name, description} — the buttons (D-042)
    Column("submitted_at", DateTime, nullable=False),
)

decision_resolutions = Table(
    "decision_resolutions",
    metadata,
    # decision_id is the PK — the first write wins the CAS; a second resolution is
    # rejected and told who already resolved (D-045, like an answer).
    Column("decision_id", String, ForeignKey("decisions.decision_id"), primary_key=True),
    Column("choice", String, nullable=False),  # the picked choice name — routes the resolving transition
    Column("resolved_by", String, nullable=False),
    Column("resolved_at", DateTime, nullable=False),
)

# --- Requeue facts (close needs_human by supersession — D-067) --------------
#
# ``blizzard hub requeue <chunk>`` appends this fact to close an open escalation by
# supersession (never a resolution fact): an escalation stays open only while no later
# lease mint AND no later requeue supersedes it (domain/work.md open_escalation). The
# requeue also releases the route so the chunk re-derives ``ready`` and re-enters FILL
# at its current node — a fresh attempt (design/cli.md).

requeues = Table(
    "requeues",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("requeued_at", DateTime, nullable=False),  # supersedes an earlier escalation (D-067)
)

# --- Store-and-forward high-water mark (per-runner idempotency — D-069) ------
#
# The hub's dedup memory for the runner→hub fact push (POST /events): the greatest
# per-runner sequence number it has already applied. A pushed fact with seq ≤ mark
# is already-applied and re-acked without re-applying — the idempotent replay after
# a lost ack or an outage backlog drain. One row per runner, advanced monotonically.

runner_high_water = Table(
    "runner_high_water",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("seq", Integer, nullable=False),  # greatest applied per-runner seq (D-069)
    Column("updated_at", DateTime, nullable=False),
)

# --- Queue shaping: ready-queue ordering (D-048/D-004) ----------------------
#
# Ready-queue ordering is an explicit hub-side property (design/hub/web-app.md
# Prioritize): the operator moves a ready chunk to a position, and GET /queue/peek
# honours it. Facts append, order derives (D-004): each reorder appends ONE row —
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
    Column("set_at", DateTime, nullable=False),
)

# --- Queue shaping: grouping (chunk.grouped — D-048/D-076/D-047) -------------
#
# Group N unacquired (ready) chunks into one surviving chunk: the survivor absorbs the
# union of their PM pointers (plural pointers per D-076, appended to chunk_pm_pointers),
# and each merged-away chunk records a ``chunk.grouped`` fact naming the survivor. A
# grouped chunk is EPHEMERAL (D-047): it is removed from every listing rather than
# deriving a status (domain/events.md), exactly like a discard — the PM item lives on
# as a pointer on the survivor.

chunk_grouped = Table(
    "chunk_grouped",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the merged-away chunk
    Column("grouped_into", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the survivor
    Column("grouped_at", DateTime, nullable=False),
)

# --- The fleet registry (runner.registered / paused / resumed — D-019/D-070/D-043) --
#
# Runners register on startup (runner_id + workspace_id) and appear on the board. The
# registration row is an upsert: ``last_seen_at`` is a refreshed timestamp (not a fact),
# bumped by the register call and the dedicated heartbeat (D-070); liveness derives from
# it against a staleness threshold (never a stored column, D-004). Operational state is
# declarative and append-only: pause/resume facts land in ``runner_pause_facts`` and
# ``paused`` derives from the newest one (D-043, the D-039 pattern) — the runner reads it
# back on its outbound pull and adheres (paused = no new claims; in-flight runs on).

runner_registrations = Table(
    "runner_registrations",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("workspace_id", String, nullable=False),  # the per-runner workspace binding (D-019)
    Column("registered_at", DateTime, nullable=False),
    Column("last_seen_at", DateTime, nullable=False),  # liveness derives from this (D-070)
)

runner_pause_facts = Table(
    "runner_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
    Column("paused", Boolean, nullable=False),  # paused derives from the newest fact (D-043)
    Column("set_at", DateTime, nullable=False),
    Column("set_by", String, nullable=False),  # who flipped it — recorded on the fact
)
