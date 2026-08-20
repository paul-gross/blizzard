"""The hub store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``): every table records a thing that
definitely happened at a definite time, and no ``status`` column exists. Timestamps are
stamped by application code from the injected clock (``bzh:injected-clock``), never a
``server_default``. Portable-SQL surface only (``bzh:sql-portable``)."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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
    # The targeted-resume source node name (issue #115) — the parsed ``<name>`` of a
    # ``session: resume:<name>`` form; null for bare ``resume``/``fresh``.
    Column("session_source", String, nullable=True),
    Column("judged_by", String, nullable=False),  # worker | human
    Column("retries_max", Integer, nullable=True),
    Column("retries_exhausted", String, nullable=True),  # escalate
    Column("mode", String, nullable=True),  # deliver hub node: merge-to-main | open-pr
    Column("produces", Text, nullable=True),  # JSON list of artifact names; e.g. review's `review-findings`
    Column("checks", Text, nullable=True),  # JSON list of check commands, runner-run at worker exit (#114)
    # Where the runner runs this node's ``checks:`` and the per-check timeout (#114) — null
    # runs at the env workdir root / accepts the check-runner's default timeout.
    Column("checks_cwd", String, nullable=True),
    Column("checks_timeout", Integer, nullable=True),
    # The kick-back cap (#64) — null accepts the fleet default (``graph.DEFAULT_BOUNCE_CAP``).
    Column("bounce_cap", Integer, nullable=True),
    # The generic hub command node's declared commands (#65) — JSON list of
    # ``{command, name, produces}``; null/empty on every other node.
    Column("run", Text, nullable=True),
    # The pending-poll cadence (#66) — null accepts the executor's own default
    # (``hub_node.DEFAULT_POLL_INTERVAL`` / ``DEFAULT_POLL_TIMEOUT``).
    Column("poll_interval_seconds", Integer, nullable=True),
    Column("poll_timeout_seconds", Integer, nullable=True),
)

graph_choices = Table(
    "graph_choices",
    metadata,
    Column("choice_id", String, primary_key=True),  # cho_<ulid>
    Column("node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
    Column("name", String, nullable=False),
    Column("description", Text, nullable=False),
    # Whether this choice is gated on green checks (#114) — null/false is ungated (every
    # pre-#114 choice). The hub backstop and the runner-local gate both read it.
    Column("requires_checks", Boolean, nullable=True),
)

graph_edges = Table(
    "graph_edges",
    metadata,
    Column("edge_id", String, primary_key=True),
    Column("from_node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
    Column("choice_id", String, ForeignKey("graph_choices.choice_id"), nullable=False),
    Column("to_node_name", String, nullable=False),  # a node name, the reserved 'done', or 'graph:<name>' (#90)
    Column("prompt_addendum", Text, nullable=True),  # inlined arrival context
    # The optional per-choice model override applied when a cross-graph migration edge
    # (#90) re-pins the chunk — null keeps the chunk's current model.
    Column("to_graph_model", String, nullable=True),
)

# The graph-level named-session declarations (issue #144) — one row per `sessions:` entry,
# keyed `(graph_id, name)`, since `name` is what every reference resolves by.
graph_sessions = Table(
    "graph_sessions",
    metadata,
    Column("graph_id", String, ForeignKey("graphs.graph_id"), primary_key=True),
    Column("name", String, primary_key=True),
    Column("ordinal", Integer, nullable=False),  # authored `sessions:` position, display-only
    # The prioritized model preference list — JSON `list[str]` of opaque preference strings.
    # The hub never interprets an entry (``bzh:pluggable-seams``).
    Column("model", Text, nullable=True),
    Column("effort", String, nullable=True),  # a single aliased value; null declares none
    # The rotation bounds — all nullable and independently declared; a declaration with no
    # `rotate:` at all leaves all three null and bounds nothing.
    Column("rotate_max_context_tokens", Integer, nullable=True),
    Column("rotate_max_transcript_bytes", Integer, nullable=True),
    Column("rotate_max_invocations", Integer, nullable=True),
    # The compaction window, opaque like `effort`; null declares none.
    Column("compaction_window", String, nullable=True),
)

# The graph-scoped `artifacts:` declarations — the `graph_sessions` shape, one row
# per entry keyed `(graph_id, name)`, not a JSON column on `graphs`.
graph_artifacts = Table(
    "graph_artifacts",
    metadata,
    Column("graph_id", String, ForeignKey("graphs.graph_id"), primary_key=True),
    Column("name", String, primary_key=True),
    Column("ordinal", Integer, nullable=False),  # authored `artifacts:` position — every read orders by it
    Column("content", Text, nullable=False),
)

# --- Graph lifecycle facts (graph.retired / graph.enabled — issue #101) -------
# The reversible retire/re-enable brake over one graph_id: append-only, newest-fact-wins.

graph_lifecycle_facts = Table(
    "graph_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),  # who flipped it — recorded on the fact
)

# The per-graph follow-latest policy (issue #164) — append-only, newest-fact-wins.
# `follow_latest` is **tri-state**: NULL (or no row at all) inherits the hub setting.
graph_policy_facts = Table(
    "graph_policy_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("follow_latest", Boolean, nullable=True),  # tri-state: null inherits the hub setting
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# --- Chunks and their work refs (chunk.minted) ------------------------------

chunks = Table(
    "chunks",
    metadata,
    Column("chunk_id", String, primary_key=True),  # ch_<ulid>
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),  # pinned at mint
    Column("minted_at", UtcDateTime, nullable=False),
    # RETAINED AND UNREAD (superseded by `default_model`/`default_effort`, issue #144):
    # a new row carries the migration's `server_default`. Never read it as a current fact.
    Column("model", String, nullable=False),
    # The chunk's **default** model preference (JSON `list[str]`) and effort (issue #144).
    # Both nullable and minted empty: an empty preference means *express none*.
    Column("default_model", Text, nullable=True),
    Column("default_effort", String, nullable=True),
    # The chunk's standing intent to migrate at its next transition (issue #124) — a JSON
    # `{"mode", "graph_id", "node_name"}` blob, read whole; NULL while no intent is set.
    Column("intended_migration", Text, nullable=True),
)

chunk_work_refs = Table(
    "chunk_work_refs",
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
    # The graph this transition happened in (issue #90) — so a later cross-graph migration
    # never strands its node ids against the new pin. No ForeignKey, like its siblings.
    Column("graph_id", String, nullable=False),
    Column("from_node_id", String, nullable=True),  # null on the first transition out of entry
    Column("to_node_id", String, nullable=False),  # a node_id, or 'done' terminal
    Column("choice_name", String, nullable=True),  # the judgement's selected choice
    Column("decision_id", String, nullable=True),  # gates only; shaped for P7
    Column("epoch", Integer, nullable=False),  # the fencing epoch checked against latest
    Column("runner_id", String, nullable=False),  # reporting author, or the hub coordinator
    Column("recorded_at", UtcDateTime, nullable=False),
)

# The activity feed's bounded read (issue #213) — indexed because ``transitions`` is the
# one high-volume source among the feed's.
Index("ix_transitions_recorded_at", transitions.c.recorded_at)

# --- Cross-graph migration record (chunk_migrations — issue #90) ---------------
# Its own fact, never a ``transitions`` row (``bzh:migration-not-transition``).

chunk_migrations = Table(
    "chunk_migrations",
    metadata,
    Column("migration_id", String, primary_key=True),  # mg_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("from_node_id", String, nullable=True),  # the node the migrating choice left
    Column("from_graph_id", String, nullable=False),  # the graph migrated out of
    Column("to_graph_id", String, nullable=False),  # the graph re-pinned to
    Column("landed_node_id", String, nullable=True),  # concrete landing node; null = target entry
    Column("choice_name", String, nullable=True),  # the triggering judgement choice
    Column("decision_id", String, nullable=True),  # gate migrations only — the decision this closes (#90)
    Column("model_after", String, nullable=True),  # the re-pinned model, or null (kept current)
    Column("epoch", Integer, nullable=False),  # the submitting fence; the natural-key third part
    Column("recorded_at", UtcDateTime, nullable=False),
    # What moved the chunk (issue #164): authored-edge | intent | follow-latest. Nullable —
    # a row predating the discriminator stays honestly unattributed.
    Column("source", String, nullable=True),
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
    Column("forge", String, nullable=True),  # git_commit only (issue #143, Phase 4); null = legacy row
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
    # The monotonic route-event tiebreak (see work.RouteHistory) — a
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

# --- Route capability tokens (route_token_minted — issue #84a) ----------------
# Only the sha256 digest is persisted; ``seq`` shares the per-chunk route counter.
route_token_minted = Table(
    "route_token_minted",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("token_hash", Text, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("minted_at", UtcDateTime, nullable=False),
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

# --- Delivery closure facts (work_item_closures — issue #216) -----------------
# One row per close-attempt outcome; `closed`/`gone` are terminal, `failed` is retried.

work_item_closures = Table(
    "work_item_closures",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("source", String, nullable=False),
    Column("ref", String, nullable=False),
    Column("outcome", String, nullable=False),
    Column("reason", String, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
    UniqueConstraint("chunk_id", "source", "ref", "outcome", name="uq_work_item_closures_chunk_source_ref_outcome"),
)

# --- Delivery kick-backs (chunk_bounces — #64) --------------------------------
# Contention, not failure: consumes no node retry, natural-keyed ``(chunk_id, epoch)``.

chunk_bounces = Table(
    "chunk_bounces",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # the coordinator's hub_epoch — the natural key
    Column("cause", String, nullable=False),  # conflict | checks | master-moved
    Column("envelope", Text, nullable=False),  # JSON kick-back payload
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Open-PR delivery facts (pr.opened / pr.closed) ---------------------------
# Read-only history (#67): no engine path writes either table any more.

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
    # One ``pr.opened`` per (chunk, repo) — the constraint that closed a replay race,
    # retained as the shape of the historical rows this table still reads back.
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

# --- The fleet-wide hub-execution serialization slot (#65) -------------------
# A live slot has ``released_at IS NULL``; one at a time, reclaimable past its TTL.

hub_exec_slot = Table(
    "hub_exec_slot",
    metadata,
    Column("slot_id", String, primary_key=True),  # hes_<ulid>
    Column("holder_chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("acquired_at", UtcDateTime, nullable=False),
    Column("released_at", UtcDateTime, nullable=True),  # null while live
)

# --- The generic hub command node's pending-poll attempts (#66) --------------
# One append-only row per poll attempt; pending-ness derives from them, never memory.

hub_node_poll = Table(
    "hub_node_poll",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("polled_at", UtcDateTime, nullable=False),
)

# --- Readiness: the not-ready resting state and its promotion --------
# A chunk with no ``chunk_promoted`` row derives ``not_ready`` and is never claimed.

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
    # Who stopped it (issue #118) — nullable: a row predating the column reads back `None`.
    Column("stopped_by", String, nullable=True),
)

# An operator's manual completion (issue #294) — outranks a ``chunk_stopped`` row recorded
# at or before it (``ChunkFacts._operator_completion_outranks_stop``), the motivating case.
chunk_completed = Table(
    "chunk_completed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("completed_at", UtcDateTime, nullable=False),
    Column("completed_by", String, nullable=False),
)

escalations = Table(
    "escalations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # closed by supersession, not a resolution
    Column("takeover_command", Text, nullable=False, server_default=""),  # the pasteable resume command
    # The runner-composed ``blizzard runner takeover`` invocation, beside the raw
    # harness-resume ``takeover_command``. Stored pre-composed; empty when none was.
    Column("wrapped_takeover_command", Text, nullable=False, server_default=""),
    # Set only when a gate's resolved choice migrated to an unresolvable target (issue
    # #110), so the gate's decision derives closed here too. Null otherwise.
    Column("decision_id", String, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Usage facts (usage.recorded — issue #59) --------------------------------
# One row per harness invocation. **Not** epoch-fenced: a zombie's spend is real spend.

usage_facts = Table(
    "usage_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),  # the row's own epoch — carried, never fenced against
    Column("runner_id", String, nullable=False),  # the reporting runner — audit/attribution only
    Column("kind", String, nullable=False),  # spawn | resume | judge | nudge
    Column("model", String, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cache_read_tokens", Integer, nullable=False),
    Column("cache_create_tokens", Integer, nullable=False),
    Column("cost_usd", Float, nullable=True),  # None = no envelope for this invocation — never fabricated
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Questions and answers (the ask/answer rendezvous) ----------------------
# Open exactly while no answer row exists; the answer is first-write-wins CAS on the PK.

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
# Resolved-ness derives: a decision with a resolution row is resolved.

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
# `escalation_superseded` owns which facts close one; there is no resolution fact.

requeues = Table(
    "requeues",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("requeued_at", UtcDateTime, nullable=False),  # supersedes an earlier escalation
)

# An operator's forced move of a chunk onto a node, now (issue #370) — a movement fact of
# its own, never a transition: nothing judged it and no edge was taken.
chunk_restarts = Table(
    "chunk_restarts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    # The graph this move happened in — a restart crosses none, but a later migration would
    # otherwise strand its node ids against the new pin, exactly as for a transition.
    Column("graph_id", String, nullable=False),
    Column("from_node_id", String, nullable=True),  # the node left behind; null before the first move
    Column("to_node_id", String, nullable=False),  # the node forced onto
    Column("epoch", Integer, nullable=False),  # the fresh fence that preempts the live attempt
    # Set when the move superseded an open gate decision, so that decision derives closed here.
    Column("decision_id", String, nullable=True),
    Column("restarted_by", String, nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Chunk pause facts (chunk.paused / chunk.resumed — issue #46) -----------
# An operator-level brake over one chunk: append-only, newest-fact-wins.

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
# The greatest per-runner seq already applied; a fact at or below it is re-acked, not applied.

runner_high_water = Table(
    "runner_high_water",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("seq", Integer, nullable=False),  # greatest applied per-runner seq
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Queue shaping: ready-queue ordering ----------------------
# A chunk's effective position is its newest fact, else its ``minted_at`` as a unix stamp.

queue_positions = Table(
    "queue_positions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("position", Float, nullable=False),  # lower sorts earlier; newest fact per chunk wins
    Column("set_at", UtcDateTime, nullable=False),
)

# --- Queue shaping: grouping (chunk.grouped) -----------------------------------
# A grouped chunk is EPHEMERAL: removed from every listing, deriving no status at all.

chunk_grouped = Table(
    "chunk_grouped",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the merged-away chunk
    Column("grouped_into", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the survivor
    Column("grouped_at", UtcDateTime, nullable=False),
)

# --- The fleet registry (runner.registered / paused / resumed) ----------------
# The registration row is an upsert; liveness derives from ``last_seen_at``.

runner_registrations = Table(
    "runner_registrations",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("workspace_id", String, nullable=False),  # the per-runner workspace binding
    Column("registered_at", UtcDateTime, nullable=False),
    Column("last_seen_at", UtcDateTime, nullable=False),  # liveness derives from this
    # The hub-minted bearer token's sha256 hex digest (issue #86a) — nullable (an
    # unenrolled runner has none), indexed for the reverse token lookup.
    Column("token_hash", Text, nullable=True, index=True),
    # The runner's configured environment-pool size (issue #69) — nullable when the runner
    # reports none. Refreshed in place on each re-registration.
    Column("env_capacity", Integer, nullable=True),
    # The runner's own browser-reachable base URL (issue #95) — nullable: a runner that
    # registers none cannot be a federation target. Refreshed in place.
    Column("public_url", Text, nullable=True),
    # The runner's allowed redirect URIs (issue #95), JSON `list[str]` — exact-matched
    # against a presented `redirect_uri` before a JWT is minted (the open-redirect guard).
    Column("redirect_uris", Text, nullable=True),
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

# The runner's *own* brake, as reported to us (issue #43) — a separate table because the
# hub only ever reads it. No ForeignKey: a fact can arrive before its registration does.

runner_local_pause_facts = Table(
    "runner_local_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, nullable=False),
    Column("paused", Boolean, nullable=False),  # locally_paused derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),  # the runner's clock, off the fact's payload
    Column("set_by", String, nullable=False),
    # The composed cause string off the fact's payload (issue #61) — nullable, since a
    # manual pause carries none.
    Column("reason", Text, nullable=True),
)

# The runner's latest sampled external-usage snapshot (issue #218) — advisory, refreshed
# in place. No ForeignKey: an orphan row is never read, but a raise would stall the rail.
runner_external_usage = Table(
    "runner_external_usage",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("sampled_at", UtcDateTime, nullable=False),
    # JSON array of {window, utilization_pct, resets_at, window_seconds} — rewritten
    # wholesale on every sample, never queried by its members.
    Column("windows", Text, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- The identity spine: users, provider identities, sessions (issue #91) -----
# ``role`` is a coarse tag expanded through a static map, never a stored permission list.

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),  # usr_<ulid>
    Column("username", String, nullable=False, unique=True),
    Column("display_name", String, nullable=False),
    Column("email", String, nullable=True),
    Column("role", String, nullable=False),  # blizzard.auth_core.Role value
    Column("created_at", UtcDateTime, nullable=False),
)

# A partial unique index (D2) — dialect-keyed rather than raw ``text()``, staying inside
# SQLAlchemy's portable DDL surface (``bzh:sql-portable``).
Index(
    "uq_users_email",
    users.c.email,
    unique=True,
    sqlite_where=users.c.email.isnot(None),
    postgresql_where=users.c.email.isnot(None),
)

# One row per (provider, subject) a user has linked (#92). ``handle`` is the provider's
# own display name at last link, refreshed on a later login.
identities = Table(
    "identities",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("provider_name", String, nullable=False),
    Column("subject", String, nullable=False),  # the provider's own stable subject id
    Column("user_id", String, ForeignKey("users.id"), nullable=False, index=True),
    Column("handle", String, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    UniqueConstraint("provider_name", "subject", name="uq_identities_provider_subject"),
)

# A hub session, resolved by its **hashed** id; the plaintext is minted once and never
# stored. Sliding expiry: `last_seen_at`/`expires_at` are refreshed in place on resolve.
sessions = Table(
    "sessions",
    metadata,
    Column("id_hash", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.id"), nullable=False, index=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Column("last_seen_at", UtcDateTime, nullable=False),
)

# --- The provider-login seam: single-use state, non-chunk auth facts (issue #92) ----

# A single-use ``state`` (decision D5), read-and-deleted in one call, so a replayed value
# can never resolve twice. Expiry is checked at read, never swept.
auth_state = Table(
    "auth_state",
    metadata,
    Column("state", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("provider_name", String, nullable=False),
    Column("return_to", String, nullable=False),
    Column("code_challenge", String, nullable=True),  # reserved for #96's PKCE public client
    Column("created_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Column("user_id", String, nullable=True),  # issue #96, cli_login rows only — see note above
)

# The append-only, non-chunk auth/security event log (``bzh:facts-not-status``) — these
# events concern no single chunk.
auth_facts = Table(
    "auth_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String, nullable=False),
    Column("actor", String, nullable=False),
    Column("subject", String, nullable=False),
    Column("detail", Text, nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- The superuser bootstrap lifecycle (issue #94) ---------------------------------
# A **singleton** row, so a config change naming a different email can still demote.
superuser_bootstrap = Table(
    "superuser_bootstrap",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String, nullable=False),
    Column("claimed_user_id", String, ForeignKey("users.id"), nullable=True),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Operational event log (event_log — issue #125) ---------------------------
# ``chunk_id`` is nullable — some events are runner-scoped. ``detail`` is opaque JSON.

event_log = Table(
    "event_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", UtcDateTime, nullable=False),
    Column("severity", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("runner_id", String, nullable=False),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=True),
    Column("lease_id", String, nullable=True),
    Column("node_name", String, nullable=True),
    Column("message", Text, nullable=False),
    Column("detail", Text, nullable=True),
)

# The read's own sort key (newest-first) — indexed so ordering never scans the table.
Index("ix_event_log_recorded_at", event_log.c.recorded_at)

# --- Transcript segments (blizzard#247, epic:transcripts) ----------------------
# One row per shipped record (D1), append-only; the natural key (D8) dedupes re-offers.

transcript_segments = Table(
    "transcript_segments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("segment_id", String, nullable=False),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("spawn_generation", Integer, nullable=False),
    Column("runner_id", String, nullable=False),
    Column("turn_range_start", Integer, nullable=False),
    Column("turn_range_end", Integer, nullable=False),
    # True on the one record that closes the segment out — never inferred from a
    # transition, since a tail may land after the step's completion (product plan).
    Column("final", Boolean, nullable=False),
    # A cap rejection (D5/D6): no content, no codec; `rejection_reason` is non-null iff
    # `rejected`.
    Column("rejected", Boolean, nullable=False),
    Column("rejection_reason", String, nullable=True),
    # Raw, uncompressed turn bytes as received (D4) — the budget currency for both caps,
    # regardless of `rejected`.
    Column("byte_count", Integer, nullable=False),
    Column("codec", String, nullable=True),  # e.g. "zlib" (D10); null iff rejected
    Column("content", LargeBinary, nullable=True),  # compressed turns JSON; null iff rejected
    Column("normalizer_version", String, nullable=False),
    Column("harness_version", String, nullable=True),
    # The runner's OWN cap declaration, distinct from `rejected` above; nullable, no backfill.
    Column("record_truncated", Boolean, nullable=True),
    # Re-ship only: the segment this replaces, which `_records_for_lease_stmt` then drops.
    Column("supersedes", String, nullable=True),
    # Hub-stamped receipt instant — the D3 rolling 24h window anchors here, never on the runner's.
    Column("received_at", UtcDateTime, nullable=False),
    UniqueConstraint("segment_id", "turn_range_start", name="uq_transcript_segments_segment_turn_start"),
)

Index("ix_transcript_segments_chunk_id", transcript_segments.c.chunk_id)
Index("ix_transcript_segments_runner_received_at", transcript_segments.c.runner_id, transcript_segments.c.received_at)
Index("ix_transcript_segments_segment_id", transcript_segments.c.segment_id)

# --- Transcript lane high-water mark (D7 — own table, not runner_high_water) --------
# `runner_high_water` belongs to the fact lane; a second lane sharing it would collide.

transcript_high_water = Table(
    "transcript_high_water",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("seq", Integer, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Derived transcript events (blizzard#254) — one row per occurrence, re-derivable ---
# from `transcript_segments` at any later extractor version (`bzh:facts-not-status`: an
# immutable observation computed from already-durable rows, never a status).

transcript_events = Table(
    "transcript_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("segment_id", String, nullable=False),
    # The extractor version that produced this row (D5/D9) — a bump re-derives history
    # while leaving earlier-version rows untouched.
    Column("extractor_version", String, nullable=False),
    Column("kind", String, nullable=False),
    # This event's location in the segment's turn tree (D8): "N" for a main-lane turn,
    # "N.M" one sidechain deep, "N.M.K" two, and so on.
    Column("turn_path", String, nullable=False),
    # Disambiguates more than one event of the same kind at the same `turn_path` — 0 for
    # every extractor today, kept general for one that could ever multi-match a turn.
    Column("occurrence", Integer, nullable=False),
    Column("payload", Text, nullable=False),  # JSON object, kind-shaped (D5, `bzh:sql-portable`)
    # `payload`'s filterable projection (blizzard#255 D1) — principal subject and
    # invoking tool; `None` for a kind with no single natural subject, never guessed.
    Column("subject", String, nullable=True),
    Column("tool", String, nullable=True),
    # Denormalized node-step context (D4), stamped at derive time.
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("spawn_generation", Integer, nullable=False),
    Column("graph_id", String, nullable=False),
    Column("depth", Integer, nullable=False),  # 0 main lane; nesting depth otherwise (D8)
    Column("agent_type", String, nullable=True),  # nearest-enclosing sidechain's; None at depth 0
    # The turn's own instant, never the hub's receipt instant; nullable (Phase 1) for an
    # untimed turn.
    Column("occurred_at", UtcDateTime, nullable=True),
    UniqueConstraint(
        "segment_id",
        "extractor_version",
        "kind",
        "turn_path",
        "occurrence",
        name="uq_transcript_events_natural_key",
    ),
)

Index("ix_transcript_events_chunk_id", transcript_events.c.chunk_id)
Index("ix_transcript_events_segment_id", transcript_events.c.segment_id)
Index("ix_transcript_events_subject", transcript_events.c.subject)
Index("ix_transcript_events_tool", transcript_events.c.tool)

# --- Per-segment derivation marker (D6) — replaced, never appended: what a segment's ---
# most recent derivation at a given extractor version saw, when, and whether it was complete.

transcript_event_derivations = Table(
    "transcript_event_derivations",
    metadata,
    Column("segment_id", String, primary_key=True),
    Column("extractor_version", String, primary_key=True),
    # A fingerprint of the segment's stored content as of this derivation (D6) — compared
    # against the segment's current fingerprint to detect a content change (a rejected
    # record later accepted, a late record landing) that the sweep must re-derive over.
    Column("content_fingerprint", String, nullable=False),
    Column("derived_at", UtcDateTime, nullable=False),
    Column("event_count", Integer, nullable=False),
    # False when the segment held a content hole (a rejected record) at derivation time —
    # declared, never silently indistinguishable from a session that read nothing (D6).
    Column("complete", Boolean, nullable=False),
)

# --- Work items (hub-owned work items — issue #357) ---------------------------
# A mutable entity row, not a fact table: title/body/edited_at change in place, and
# closure is recorded on the row itself (nullable ``closed_at`` + ``closure``) rather
# than a separate append-only table, because there is exactly one current state to read
# back — never a history of edits (``bzh:facts-not-status``, Recorded position).

work_items = Table(
    "work_items",
    metadata,
    Column("work_item_id", String, primary_key=True),  # wi_<ulid>
    Column("source", String, nullable=False),  # the WorkRef.source that owns this item ("hub")
    Column("ref", String, nullable=False),  # the WorkRef.ref, allocated from work_item_sequence
    Column("title", String, nullable=False),
    Column("body", Text, nullable=False),
    # Discriminator + one JSON payload read whole, matching artifacts.kind/data and
    # transcript_events.kind/payload (`bzh:sql-portable`) — a hub user by id, or the
    # fleet itself.
    Column("author_kind", String, nullable=False),  # user | fleet
    Column("author_payload", Text, nullable=False),  # JSON object, author_kind-shaped
    # A plain, comment-documented value set rather than a DB enum (`bzh:sql-portable`):
    # low | normal | high. Null — the author stated none.
    Column("stated_priority", String, nullable=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("edited_at", UtcDateTime, nullable=False),
    # Unset while open. Set together, once, when the item closes.
    Column("closed_at", UtcDateTime, nullable=True),
    Column("closure", String, nullable=True),  # delivered | withdrawn
    UniqueConstraint("source", "ref", name="uq_work_items_source_ref"),
)

Index("ix_work_items_source", work_items.c.source)

# A per-source allocation counter, one row per source, so ``ref`` allocation never
# reads ``MAX(ref)+1`` (two concurrent first allocations on an empty source would both
# compute 1) — every source gets a pre-existing row instead (`bzh:sql-portable`).

work_item_sequence = Table(
    "work_item_sequence",
    metadata,
    Column("source", String, primary_key=True),
    Column("next_ref", Integer, nullable=False),
)
