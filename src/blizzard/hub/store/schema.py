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
transition points back at — as is ``chunk_migrations.decision_id`` when a gate's
resolved choice migrates cross-graph (issue #90), so that decision derives closed
even though a migration writes no transitions row.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
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
    # The targeted-resume source node name (issue #115) — the parsed ``<name>`` of a
    # ``session: resume:<name>`` form; null for bare ``resume``/``fresh``.
    Column("session_source", String, nullable=True),
    Column("judged_by", String, nullable=False),  # worker | human
    Column("retries_max", Integer, nullable=True),
    Column("retries_exhausted", String, nullable=True),  # escalate
    Column("mode", String, nullable=True),  # deliver hub node: merge-to-main | open-pr
    Column("produces", Text, nullable=True),  # JSON list of artifact names; e.g. review's `review-findings`
    Column("checks", Text, nullable=True),  # JSON list of check commands, worker-run in-session
    # The kick-back cap (#64) — null accepts the fleet default (``graph.DEFAULT_BOUNCE_CAP``).
    Column("bounce_cap", Integer, nullable=True),
    # The generic hub command node's declared commands (#65) — JSON list of
    # ``{command, name, produces}``; null/empty on every node but a generic hub
    # command node (the still-special deliver node included).
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
    # (#90) re-pins the chunk — null keeps the chunk's current model. The cross-graph
    # *target* itself needs no column: it rides in ``to_node_name`` as ``graph:<name>``
    # and is re-derived on load (``graph.target_graph_of``); the model, not being encoded
    # there, is the one authored value a migration edge must persist separately.
    Column("to_graph_model", String, nullable=True),
)

# --- Graph lifecycle facts (graph.retired / graph.enabled — issue #101) -------
#
# The reversible retire/re-enable brake over one specific graph_id, keyed the same way
# chunk_pause_facts mirrors runner_pause_facts: append-only, newest-fact-wins. The
# graphs table itself stays insert-only and immutable — retiring never touches it, it
# only excludes the graph_id from name resolution (get_enabled_by_name/mark_effective).

graph_lifecycle_facts = Table(
    "graph_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),  # who flipped it — recorded on the fact
)

# --- Chunks and their PM pointers (chunk.minted) ------------------------------

chunks = Table(
    "chunks",
    metadata,
    Column("chunk_id", String, primary_key=True),  # ch_<ulid>
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),  # pinned at mint
    Column("minted_at", UtcDateTime, nullable=False),
    # The model selection — pinned at mint, editable while the chunk rests `not_ready`
    # or sits `ready` unclaimed (issue #27, #120, domain/edit.py). A plain mutable
    # column, not a fact log — mirrors `graph_id` above, which was already
    # mutable-at-mint with no fact table behind it.
    Column("model", String, nullable=False),
    # The chunk's standing intent to migrate onto another graph at its next transition
    # (issue #124) — nullable, NULL while no intent is set. A single JSON `Text` column
    # (`{"mode", "graph_id", "node_name"}`) rather than a fact table: it is a plain
    # mutable property read whole at consult time, the same `bzh:facts-not-status` shape
    # `graph_id`/`model` already carry, and nothing filters on its contents
    # (`bzh:sql-portable`).
    Column("intended_migration", Text, nullable=True),
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
    # The graph this transition happened in (issue #90 — graph-provenance). A movement
    # fact carries the identity of the graph it moved within, so a later cross-graph
    # migration (which re-pins ``chunks.graph_id``) never strands an old-graph transition's
    # node ids against the new pin: hydration resolves each transition's executor/name
    # against *its own* graph, not the chunk's current one. No ForeignKey — the sibling
    # ``from_node_id``/``to_node_id`` columns carry none either (``to_node_id`` may be the
    # reserved terminal), and the invariant-checker seeds transitions with a bare graph id.
    Column("graph_id", String, nullable=False),
    Column("from_node_id", String, nullable=True),  # null on the first transition out of entry
    Column("to_node_id", String, nullable=False),  # a node_id, or 'done' terminal
    Column("choice_name", String, nullable=True),  # the judgement's selected choice
    Column("decision_id", String, nullable=True),  # gates only; shaped for P7
    Column("epoch", Integer, nullable=False),  # the fencing epoch checked against latest
    Column("runner_id", String, nullable=False),  # reporting author, or the hub coordinator
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Cross-graph migration record (chunk_migrations — issue #90) ---------------
#
# A judgement choice targeting another graph (``to: graph:<name>``) re-pins the chunk
# and re-queues it — its **own recorded fact**, never a ``transitions`` row
# (``bzh:migration-not-transition``: a transitions row whose two nodes span graphs is
# exactly the violation this table avoids). ``record_migration`` writes this fact, the
# ``chunks.graph_id`` (+ ``chunks.model`` when re-pinned) update, the ``route_released``,
# and the submitting node-step's artifacts in **one transaction**, idempotent-guarded by
# ``(chunk_id, from_node_id, epoch)`` — a crash-replay re-enters harmlessly. ``epoch`` is
# the submitting attempt's fence (the next claim mints a fresh higher one above it);
# ``landed_node_id`` is the concrete name-match-else-entry landing node in the target
# graph (nullable only as the schema allowance for "the target's entry"); ``model_after``
# is the re-pinned model, or null when the migration kept the chunk's current model;
# ``choice_name`` is the triggering choice (paralleling ``transitions.choice_name``).
# ``decision_id`` is set only when a **human gate's** resolved choice is itself the
# cross-graph migration (issue #90): a migration writes no ``transitions`` row, so
# without pointing back at the decision here it would stay ``transitioned=False`` forever
# (a phantom live decision that mis-renders the board and wedges REAP recovery). It is
# null for the ordinary worker-judged migration, which resolves no decision.

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

# --- Route capability tokens (route_token_minted — issue #84a) ----------------
#
# An unguessable per-acquisition secret minted alongside a claim's ``route_created``
# fact — an **append-only fact table**, deliberately not a ``token_hash`` column on
# ``route_created`` (``bzh:facts-not-status``): the route fact is immutable, so a
# re-key (Phase 6) must append a new token fact, never rewrite the route row, the same
# reason ``route_released`` is its own table rather than a status flip. Only the
# sha256 hex digest is ever persisted; the plaintext is returned once in the claim
# response and never stored. ``seq`` shares :func:`ChunkStore._next_route_seq`'s
# per-chunk counter with ``route_created``/``route_released`` (not a private one) so a
# token fact totally orders against a create/release even on a timestamp tie — the
# hub's own live-token derivation (``hub/domain/work.py``'s ``newest_live_route_token``)
# depends on that shared ordering to resolve exactly one live token per live route.
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

# --- Delivery kick-backs (chunk_bounces — #64) --------------------------------
#
# A delivery kick-back (conflict / CI-red / master-moved) is contention, not
# failure: it consumes no node retry and triggers no escalation by itself. Every
# kick-back appends one row here, independent of the transition that routes the
# chunk back to a worker node (or, once the chunk's ``bounce_count`` crosses its
# node's ``bounce_cap``, the escalation that replaces that routing) — so the count is
# derived purely from these rows (``bzh:facts-not-status``) and a redelivery replay
# after a crash is guarded by the natural key ``(chunk_id, epoch)`` (the coordinator's
# own ``hub_epoch``), never double-counted. ``envelope`` is the opaque JSON kick-back
# payload (cause, per-repo detail) carried into the fix node's arriving artifacts so it
# never rediscovers what bounced it.

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
#
# Pre-#67 history, kept for back-compat reads of a chunk delivered before the generic
# hub command node executor: the ``open-pr`` deliver mode recorded ``pr.opened`` here
# without a terminal transition or ``route_released``, so the chunk derived
# ``delivering`` (awaiting an external merge) with its environments held, and a later
# poll recorded ``pr.closed`` — the terminal fact that flipped the chunk to ``done``
# (either disposition), carrying ``merged`` and the actually-landed commit where one
# exists. No engine path writes either table any more; a hub command node's own
# ``run:`` script (e.g. ``hub/graphs/scripts/land_pr_ci.py``) owns this policy now.

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
    # A ``pr.opened`` write was idempotent per (chunk, repo) (20260716_2206_hub_pr_opened_idempotent):
    # the now-deleted coordinator's deliver node ran on both a fresh apply and an idempotent
    # replay, and its DB-backed skip-set (a store read each call, not an in-memory cache) had a
    # narrow race between that read and the write. This constraint was the actual close of that
    # race. Retained now only as the shape of the historical rows this table still reads back
    # (see the table-group comment above) — no engine path writes it any more.
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
#
# A generic hub command node's ``run:`` list executes serialized fleet-wide — one
# chunk's hub node running at a time is what makes merging safe. The slot is a FACT
# (``bzh:facts-not-status``), not an in-process lock: acquire-if-none-live under
# SQLite's single-writer transaction, released at end-of-run. A live slot is a row
# with ``released_at IS NULL``; at most one may exist at a time (the invariant checker
# asserts this after any crash). A slot older than its holder's staleness TTL
# (measured against the injected clock, never wall time) is reclaimable — a kill -9
# mid-run leaves a slot no later run will ever release, so a stale live slot is treated
# as free rather than wedging the fleet forever.

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
#
# A hub command node whose ``run:`` step reports the reserved ``pending`` outcome
# records no transition — it appends one row here instead, releases the fleet-wide
# ``hub_exec_slot`` immediately, and is re-run once ``poll_interval`` has elapsed. Every
# row is one poll attempt, append-only, stamped from the injected clock
# (``bzh:injected-clock``): pending-ness is DERIVED (``hub_node_pending`` in
# ``hub/domain/work.py``) from "the newest transition still enters this hub node AND a
# poll fact exists for its (node_id, epoch)" — nothing in-memory, so a ``kill -9``
# between polls resumes polling straight from these rows. ``epoch`` is the arrival
# epoch of the current visit (the same value the node's own marker/log artifacts are
# recorded under, ``hub_node.HubNodeExecutor``), not a fresh one minted per poll.

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
#
# Ingest mints a chunk in a NOT-READY resting state: visible on the board but
# never claimed by a runner. A ``chunk.promoted`` fact — appended by ``POST
# /chunks/{id}/promote`` — flips it to ``ready`` (facts append, status derives). An
# un-promoted chunk with no ``chunk_promoted`` row derives ``not_ready`` and so is
# excluded from ``list_ready``/``GET /api/queue``; existing chunks predating this table
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
    # Who stopped it (issue #118) — nullable: rows written before this column existed
    # (including a hand-written `INSERT` against a live store) predate it and read back
    # as `None`, mirroring `runner_local_pause_facts.reason` (issue #61).
    Column("stopped_by", String, nullable=True),
)

escalations = Table(
    "escalations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # closed by a later lease mint, not a resolution
    Column("takeover_command", Text, nullable=False, server_default=""),  # the pasteable resume command
    # ``decision_id`` is set only when a **human gate's** resolved choice migrated
    # cross-graph to an unresolvable target (issue #110): the escalation stands in for the
    # migration fact the resolvable branch writes (issue #90), so the gate's decision
    # derives ``transitioned`` (closed) here too — neither a migration nor a transition row
    # exists to close it, and an unclosed gate decision wedges REAP recovery and drives a
    # per-tick runner re-submit. Null for the ordinary retries-exhausted escalation.
    Column("decision_id", String, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Usage facts (usage.recorded — issue #59) --------------------------------
#
# One append-only row per harness invocation's usage/cost telemetry the runner
# reported up, ridden on the same store-and-forward rails as ``lease_facts``. Never
# aggregated here: a chunk's total is derived at read time by summing these
# (``derive_chunk_usage``, ``bzh:facts-not-status``). Deliberately **not** epoch-fenced —
# unlike ``lease_facts``/``escalations``, a stale-epoch row still lands: it is real spend
# by a fenced-out zombie attempt, not a rejected transition. No dedup column: the runner's
# per-runner outbound-buffer seq high-water mark already makes a replayed batch land
# each fact exactly once, so a second idempotency key here would be redundant.

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
# control): the operator moves a ready chunk to a position, and GET /api/queue
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
    # The hub-minted bearer token's sha256 hex digest (issue #86a) — nullable because an
    # unenrolled runner (every runner before an operator's `enroll` call, and every
    # pre-#86a row) has none. Indexed because `registration_for_token_hash` selects on
    # it — the reverse lookup `require_runner_principal` resolves a presented token
    # through, the mirror image of every other registry read (which key on
    # `runner_id`, already primary-keyed). A rotating column, not an append-only fact
    # (`bzh:facts-not-status`'s one deliberate exception — see this table's module
    # docstring): the registration row is already a mutable upsert, so re-enrollment
    # overwriting the hash in place is consistent with the rest of the row, unlike the
    # route capability token (#84's append-only fact table).
    Column("token_hash", Text, nullable=True, index=True),
    # The runner's configured environment-pool size (issue #69) — the `total` the board's
    # slot bar renders `used/total` against. Nullable: a runner registered by a client that
    # predates this field reports none, and the board omits the bar rather than guess a
    # total. A reported fact refreshed in place on each re-registration (like `last_seen_at`
    # / `token_hash` above), so a `workspace_envs` change converges on the next pull.
    Column("env_capacity", Integer, nullable=True),
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
    # The composed cause string off the fact's payload (issue #61's spend-ceiling escalation
    # names the ceiling + spend here) — nullable because a manual `blizzard runner pause`
    # carries none, and every pre-#61 row predates the column.
    Column("reason", Text, nullable=True),
)

# --- The identity spine: users, provider identities, sessions (issue #91) -----
#
# Independent of any login mechanism (that is #92) — the schema every other auth slice
# builds on. ``users`` is the hub-local account row: ``username`` is unique (minted from
# a provider handle with a collision suffix once #92 lands), ``role`` is the coarse
# ``blizzard.auth_core.Role`` tag a session's ``ResolvedIdentity`` expands via the
# static ``ROLE_PERMISSIONS`` map (never a stored permission list — `bzh:domain-core`).
# ``email`` is nullable (a provider need not disclose one) and, when present, unique —
# enforced by the partial index below rather than a plain column-level UNIQUE, since a
# plain UNIQUE treats every NULL as equal-and-conflicting on some backends.

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

# A partial unique index (D2): ``WHERE email IS NOT NULL`` via SQLAlchemy's
# dialect-keyed ``sqlite_where``/``postgresql_where`` on one ``Index`` — both backends
# honor a partial index, and setting both keeps this inside SQLAlchemy's portable DDL
# surface (``bzh:sql-portable``), no raw ``text()``. A plain ``unique=True`` column
# constraint cannot express "unique only when set" portably.
Index(
    "uq_users_email",
    users.c.email,
    unique=True,
    sqlite_where=users.c.email.isnot(None),
    postgresql_where=users.c.email.isnot(None),
)

# One row per (provider, subject) a user has linked — the first-login email-merge rule
# (#92) attaches a second identity to an existing user; nothing in #91 writes one yet
# (sessions are seeded directly by tests). ``handle`` is the provider's own
# display/login name at last link, refreshed on a later login (#92) rather than
# re-derived here.
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

# A hub session — the browser cookie / CLI bearer resolves to one of these by its
# **hashed** id (sha256, `hub/auth/hashing.py`); the plaintext is minted once and never
# stored (mirrors `runner_registrations.token_hash`, issue #86a). Sliding expiry:
# `last_seen_at`/`expires_at` are refreshed in place on each resolve
# (`AuthService.touch_session`), under an absolute-max cap enforced by the domain, not
# a second column.
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

# A single-use ``state`` round-tripped through a provider redirect (decision D5).
# ``authorize`` writes one; ``callback`` reads-and-deletes it in one call
# (``AuthStateRepository.consume``), so a replayed ``state`` query parameter can never
# resolve twice. ``kind`` distinguishes this table's callers (``"provider_login"``
# here; the same table is reused, unmodified, by #95's hub-as-IdP authorize).
# ``provider_name`` cross-checks the callback's own ``{name}`` path segment. No
# separate cleanup job removes expired rows — a stale row simply never resolves
# (``expires_at`` is checked at read, not swept).
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
)

# The append-only, non-chunk auth/security event log (``bzh:facts-not-status``) —
# `login_failed`/`sso_refused` land here in this phase; #94 adds `user_role_changed`.
# Distinct from the chunk-scoped fact tables: these events concern no single chunk.
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
