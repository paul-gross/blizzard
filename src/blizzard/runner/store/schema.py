"""The runner store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``): the machine-local fast path
 — leases with their pid + process-start-time, chunk->env bindings,
and the store-and-forward outbound buffer. Timestamps come from the injected clock,
never a ``server_default`` (``bzh:injected-clock``); portable-SQL surface only
(``bzh:sql-portable``).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

from blizzard.foundation.store.utc import UtcDateTime

metadata = MetaData()

# --- Leases (the machine's execution right now) -----------------------------
#
# The lease carries the pid + process start time: pid alone is ambiguous across reuse,
# so the liveness check keys on (pid, process_start_time).

leases = Table(
    "leases",
    metadata,
    Column("lease_id", String, primary_key=True),  # lease_<ulid>
    Column("chunk_id", String, nullable=False),  # the chunk this lease attempt is for
    Column("epoch", Integer, nullable=False),  # incrementing fence, reported to the hub
    Column("runner_id", String, nullable=False),
    Column("pid", Integer, nullable=True),  # filled at spawn-return
    Column("process_start_time", String, nullable=True),  # stable across pid reuse; REAP keys on it
    Column("session_id", String, nullable=True),  # harness-assigned, recorded at spawn-return
    Column("created_at", UtcDateTime, nullable=False),
)

# --- Environment bindings (chunk -> env ids, from the provider) -------------

env_bindings = Table(
    "env_bindings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, nullable=False),
    Column("environment_id", String, nullable=False),  # opaque provider id
    Column("workdir", String, nullable=False),  # provider-returned working directory
    Column("bound_at", UtcDateTime, nullable=False),
)

# --- Outbound buffer (store-and-forward, per-runner monotonic seq) ----------
#
# Every hub-bound fact is written here at mint, stamped with a monotonic sequence,
# even when the hub is reachable: one flusher drains it in FIFO order, so a lease
# fact always precedes the completion minted under it — a structural guarantee of
# the FIFO drain, not a runtime check. A semantic rejection still advances the ack —
# rejection is an outcome, not a delivery failure. ``acked_at`` NULL means still
# pending. ``lease_id`` correlates a buffered fact back to its attempt.

outbound_buffer = Table(
    "outbound_buffer",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # per-runner monotonic
    Column("kind", String, nullable=False),  # lease.minted | completion.submitted | escalation.recorded
    Column("chunk_id", String, nullable=True),  # the correlated chunk, when the fact has one
    Column("lease_id", String, nullable=True),  # the correlated attempt, when the fact has one
    Column("payload", Text, nullable=False),  # the JSON body posted to the matching hub route
    Column("created_at", UtcDateTime, nullable=False),
    Column("acked_at", UtcDateTime, nullable=True),  # NULL = pending; set when the hub acks the seq
)

# --- Heartbeats (progress detection, machine-local — never leaves the box) ----
#
# A worker heartbeats as a side effect of working — one row per tool call.
# Append-only (``bzh:facts-not-status``): the *last* heartbeat for a lease is
# ``max(beat_at)``. Never travels to the hub: ``stalled`` is a runner-local derivation.

heartbeats = Table(
    "heartbeats",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # the attempt the beat belongs to (BLIZZARD_LEASE_ID)
    Column("beat_at", UtcDateTime, nullable=False),  # injected-clock stamp of the tool call
)

# --- Lease node context (the node identity of each attempt — the walking-skeleton revision's leases lacks it) -
#
# The node a lease attempts, and the retry budget that node carries. Written once per
# lease at mint. Append-only, one row per lease — a lease is one node-step attempt.

lease_context = Table(
    "lease_context",
    metadata,
    Column("lease_id", String, primary_key=True),  # 1:1 with leases.lease_id
    Column("chunk_id", String, nullable=False),
    Column("graph_id", String, nullable=False),
    Column("node_id", String, nullable=False),  # which node this attempt is at
    Column("node_name", String, nullable=False),
    Column("retries_max", Integer, nullable=False),  # the node's retry budget, from the envelope
    # What session this attempt ran, and under what configuration (issue #144). The
    # declared pool name (null for the bare/`resume:<node>` forms, which belong to no
    # pool), and the model/effort the session ACTUALLY ran under — never the freshly
    # resolved preference, which on a resume would describe a configuration the running
    # process never saw. NULL means *unknown*, never a value.
    Column("session_name", String, nullable=True),
    Column("resolved_model", String, nullable=True),
    Column("resolved_effort", String, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Lease spawns (the spawn generation of each attempt — issue #13) ----------
#
# `record_spawn` rewrites the lease's pid/session in place, so the lease alone cannot say
# *when* its current process was spawned. A lease outlives its sessions — the ask/answer
# and resume paths re-spawn under the same lease_id and session_id — so a per-lease fact
# that is true "forever after" cannot be read as true "of the process running now".
#
# Append-only, one row per spawn: the newest `spawned_at` for a lease is its current
# spawn generation, and what the session-end check is scoped to.

lease_spawns = Table(
    "lease_spawns",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # the attempt this process was spawned for
    Column("spawned_at", UtcDateTime, nullable=False),  # injected-clock stamp of the spawn-return
)

# --- Lease closures (a lease is closed iff a closure fact exists — facts-not-status) -
#
# Append-only: an active lease is one with no closure. `reason` distinguishes a
# clean node transition (`transitioned`) from an execution-attempt failure that
# counts against the node's retries (`reaped`, `failed`) and a retries-exhausted
# escalation (`escalated`).

lease_closures = Table(
    "lease_closures",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("reason", String, nullable=False),  # transitioned | reaped | failed | escalated
    Column("closed_at", UtcDateTime, nullable=False),
)

# --- Binding releases (a binding is released iff a release fact exists) --
#
# An env binding rides the chunk's tenure; it is freed only when the chunk leaves
# the runner (terminal, stop, detach). The release truth lives here as a runner-store
# fact. Held env ids are `env_bindings` minus `binding_releases`.

binding_releases = Table(
    "binding_releases",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, nullable=False),
    Column("environment_id", String, nullable=False),
    Column("released_at", UtcDateTime, nullable=False),
)

# --- Asks (the worker's local open-ask fact) ---------------------------------
#
# The ask is recorded before the worker exits, so it is durable by the time the process
# ends. The runner mints the ``question_id`` here so it can poll the hub for the answer
# by it. An ask is *unforwarded* (awaiting park) until a park_fact references its
# question_id.

asks = Table(
    "asks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # BLIZZARD_LEASE_ID the worker inherited
    Column("chunk_id", String, nullable=False),
    Column("question_id", String, nullable=False),  # qn_<ulid>, runner-minted
    Column("question", Text, nullable=False),
    Column("options", Text, nullable=False),  # JSON list[str] (may be empty)
    Column("session_id", String, nullable=True),  # the session to resume around the answer
    Column("asked_at", UtcDateTime, nullable=False),
)

# --- Park / resume (the chunk's dormancy on a question) ----------------------
#
# A lease is *parked* while a park_fact references it with no later park_resume: the
# worker asked and exited (ask-and-exit), so there is no live worker. The answer's
# arrival records a park_resume, the dormant session is resumed, and the lease is live
# again (a fresh pid recorded via record_spawn).

park_facts = Table(
    "park_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("question_id", String, nullable=False),  # the ask this park is on
    Column("parked_at", UtcDateTime, nullable=False),
)

park_resumes = Table(
    "park_resumes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("question_id", String, nullable=False),
    Column("resumed_at", UtcDateTime, nullable=False),
)

# --- Pause park / resume (the chunk's dormancy on an operator pause — issue #46) --
#
# A SEPARATE table pair from park_facts/park_resumes above, not a nullable
# ``question_id`` on that table: ``unforwarded_ask`` reads ``asks.question_id NOT IN
# (select question_id from park_facts)``, and one NULL in that subquery makes the
# predicate NULL for *every* row. Pinned by
# tests/test_pin_runner_store.py::test_pause_parks_are_their_own_table_and_park_facts_keeps_a_non_null_question_id.

pause_parks = Table(
    "pause_parks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("parked_at", UtcDateTime, nullable=False),
)

pause_park_resumes = Table(
    "pause_park_resumes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("resumed_at", UtcDateTime, nullable=False),
)

# --- Resume intent (the restart resume marker) -------------------------------
#
# A restart marks every active, non-parked, session-bearing lease with a resume-intent, so
# the startup RESUME step can resume the session in place under the **unchanged**
# ``lease_id``/``epoch``/``session_id``. Not a retry, so it consumes no retry budget.
#
# Two paths write the intent: a graceful shutdown, before the daemon exits (#12), and
# ``host``'s startup crash-recovery scan, for an ungraceful stop that never ran shutdown
# code (#13). The RESUME step is indifferent to which path marked it.
#
# Facts-only (``bzh:facts-not-status``), mirroring park/park_resume: an intent is *open*
# while a ``resume_intents`` row has no ``resume_clears`` for the same lease at or after
# it, so a later restart of a still-in-flight lease marks it afresh above that clear.

resume_intents = Table(
    "resume_intents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("marked_at", UtcDateTime, nullable=False),
)

resume_clears = Table(
    "resume_clears",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("cleared_at", UtcDateTime, nullable=False),
)

# --- Session-end signal (the durable "declared done" fact) -------------------
#
# A row here means the worker **declared done** (exit-is-done): the harness's session-end
# hook fires only on a natural session exit. A worker killed mid-work never runs the hook,
# so it has no row — and that *absence*, paired with a dead pid, is how startup tells a
# crash to resume (:func:`mark_crash_resume_intents`) from a clean exit. Append-only,
# machine-local (never travels to the hub), mirroring ``heartbeats``
# (``bzh:facts-not-status``): a lease "ended" iff a row exists.

session_ends = Table(
    "session_ends",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # BLIZZARD_LEASE_ID the SessionEnd hook inherited
    Column("ended_at", UtcDateTime, nullable=False),  # injected-clock stamp of the session's exit
)

# --- Hub control mirror (the declarative pause brake read on PULL) -----------
#
# The hub-owned pause brake, mirrored here on PULL. Mirroring it in the store keeps the
# read a machine-local, crash-safe fact: the last-known directive holds while the hub is
# unreachable. One upserted row per runner; ``paused`` is the value, ``updated_at`` when
# PULL last refreshed it.

hub_control = Table(
    "hub_control",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("paused", Boolean, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Local pause facts (the runner's own brake — issue #43) -------------------
#
# The runner's own half of the pause control (``PATCH /runner``): adhered to without the
# hub knowing or being reachable. Distinct from ``hub_control`` above in both concept and
# shape: that mirrors a hub-owned value, so it upserts; this is a locally-minted fact, so
# pause/start facts **append** and the flag derives from the newest. Effective paused is
# the OR of the two. ``set_by`` records who flipped it, on the fact.

local_pause_facts = Table(
    "local_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, nullable=False),
    Column("paused", Boolean, nullable=False),  # locally paused derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# --- Workspace prompt override (the runtime-settable spawn preamble — issue #17) --
#
# The standing workspace prompt's *runtime* override, written by the local API
# (``PUT /api/workspace-prompt``), so a replacement takes effect on subsequent spawns with
# no restart; its static source is config. One upserted row per workspace (the runner is
# single-workspace), mirroring ``hub_control``'s shape. A present row (including an empty
# ``prompt``) is a deliberate override that wins over the static config; no row means
# "never overridden — fall back to config".

workspace_prompt = Table(
    "workspace_prompt",
    metadata,
    Column("workspace_id", String, primary_key=True),
    Column("prompt", Text, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Daemon liveness (when the runner was last known alive — issue #13) -------
#
# The crash-time reference startup recovery measures staleness against, rather than
# `now - last_heartbeat` — which would read every in-flight lease as stalled after an
# outage longer than the staleness threshold. Pinned by
# tests/test_runner_crash_resume.py::test_marks_worker_killed_before_a_long_outage.
#
# The tick stamps this each pass (~30s), one upserted row per runner. No row means
# "never ticked": recovery falls back to the wall clock, only reachable on a store with
# no in-flight leases to misjudge.

daemon_liveness = Table(
    "daemon_liveness",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("alive_at", UtcDateTime, nullable=False),  # injected-clock stamp of the newest tick
)

# --- Takeovers (the operator's interactive session over a parked chunk — issue #52) --
#
# Recorded **before** any kill and before the interactive command is returned, so no later
# tick can race the human for the chunk (facts-not-status). ``lease_id`` is the lease taken
# over, when one exists (a live worker, force-killed, or a dormant ask-parked lease);
# ``None`` for the needs_human and gate-parked shapes, whose lease already closed before the
# takeover. Mirrors ``asks``' natural-key openness (a fresh ``takeover_id`` per open, unlike
# a pause's key-less fact pair): a plain ``takeover_id NOT IN (select takeover_id from
# takeover_ends)`` is safe here, since a chunk cannot carry two simultaneously-open
# takeovers (the open check refuses a second one).
#
# ``fence_epoch`` is set only on a **forced** takeover of a live worker: the epoch reported
# to the hub via a ``lease.minted``-kind outbound fact, fencing the killed worker's in-flight
# completion without minting a ``lease_context`` row — so ``attempt_count`` (the retry
# budget) never sees it, while ``latest_epoch`` still folds it in. ``None`` on a non-forced
# takeover of an already-dormant lease, which needs no fence. Pinned by
# tests/test_runner_takeover.py::test_forced_takeover_orders_fact_before_kill_fences_the_epoch_and_consumes_no_retry.

takeovers = Table(
    "takeovers",
    metadata,
    Column("takeover_id", String, primary_key=True),  # tko_<ulid>
    Column("chunk_id", String, nullable=False),
    Column("lease_id", String, nullable=True),  # the lease taken over, if any
    Column("session_id", String, nullable=True),  # the session the interactive command resumes
    Column("workdir", String, nullable=False),
    Column("fence_epoch", Integer, nullable=True),  # set only when a live worker was force-killed
    Column("opened_at", UtcDateTime, nullable=False),
)

takeover_ends = Table(
    "takeover_ends",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("takeover_id", String, nullable=False),
    Column("ended_at", UtcDateTime, nullable=False),
)

# --- Requeues (the operator's explicit hand-back after a human hold — issue #53) ----
#
# The fact appended to clear a chunk's local needs_human hold — whether the chunk is
# escalated outright or was escalated and is now held by an *ended* takeover; either way
# the underlying shape is the same closed-``escalated`` lease with no later mint
# (``domain/requeue.py``), so one fact and one openness predicate cover both. Facts-only
# (``bzh:facts-not-status``): a requeue mark is *pending* while no later lease was minted
# for the chunk, so the next fresh spawn consumes it. Distinct from the hub's own
# ``requeues`` table: this mark never leaves the runner and never touches the route.

requeues = Table(
    "requeues",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, nullable=False),
    Column("requeued_at", UtcDateTime, nullable=False),  # supersedes an earlier escalation
)

# --- Usage facts (harness cost/token telemetry per invocation — epic #57 / issue #58) --
#
# One append-only row per harness invocation (spawn / resume / judge) whose usage was
# extracted — never fabricated (``bzh:facts-not-status``). Keyed on
# ``(lease_id, generation, kind)``: ``generation`` is this lease's spawn ordinal
# (``lease_spawns``' own counting, issue #13) — a resume within the same lease mints a new
# generation and so a genuinely new row, while a replay of the exact same invocation finds
# the row already there and writes nothing twice (``record_usage``'s own check, not a DB
# constraint — the store stays portable-SQL, ``bzh:sql-portable``). ``cost_usd`` NULL is an
# honest "unknown", read by a summing caller as a lower bound, never as zero.

usage_facts = Table(
    "usage_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("generation", Integer, nullable=False),  # this lease's spawn ordinal (1 = the initial spawn)
    Column("kind", String, nullable=False),  # spawn | resume | judge | nudge
    Column("model", String, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cache_read_tokens", Integer, nullable=False),
    Column("cache_create_tokens", Integer, nullable=False),
    Column("cost_usd", Float, nullable=True),  # None = no envelope for this invocation — never fabricated
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Route capability tokens (the runner's stash — issue #84a) ----------------
#
# The plaintext of a won claim's route token (`wire.route.RouteClaimResponse.route_token`),
# stashed here on a won claim (FILL and the interrupted-claim reclaim path) and stamped
# onto every chunk-scoped outbound payload at enqueue — completion, decision, lease.minted,
# escalation.recorded, question.asked. One upserted row per chunk, mirroring `hub_control`'s
# shape: a fresh claim (a re-claim after release) overwrites the prior token; a same-runner
# requeue/takeover/retry re-reads the same row, since it re-spawns under the route already
# held rather than re-claiming. Only the *current* plaintext is kept — no rotation history,
# because the runner only ever presents its current token. Pinned by
# tests/test_pin_runner_store.py::test_set_route_token_keeps_one_current_row_per_chunk.

route_tokens = Table(
    "route_tokens",
    metadata,
    Column("chunk_id", String, primary_key=True),
    Column("token", Text, nullable=False),
    Column("acquired_at", UtcDateTime, nullable=False),
)

# --- Lease capability tokens (issue #113, Phase 1) ---------------------------
#
# A per-lease capability token minted alongside the lease itself, authorizing a later
# attach call to prove it is the worker holding this lease. One row per lease
# (``lease_id`` PK), storing only the sha256 hash; the plaintext rides the spawn env
# (``BLIZZARD_LEASE_TOKEN``) and is never persisted.

lease_tokens = Table(
    "lease_tokens",
    metadata,
    Column("lease_id", String, primary_key=True),
    Column("token_hash", Text, nullable=False),
    Column("minted_at", UtcDateTime, nullable=False),
)

# --- Attachments (a worker's explicit artifact submission — issue #113, Phase 2) ---
#
# A worker's explicit artifact submission, authorized by the lease's own capability token
# (``lease_tokens`` above). Append-only, latest-wins-per-``(lease_id, name)``
# (``bzh:facts-not-status``): a worker may re-submit the same name (a correction) and the
# newest row for that pair is the one a reader sees — no update-in-place, mirroring the
# append-and-read-newest convention the rest of this store follows. ``chunk_id`` /
# ``node_id`` / ``epoch`` are carried off the lease at attach time (denormalized, not
# joined back) so a later read needs no join to ``lease_context``.

attachments = Table(
    "attachments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("name", String, nullable=False),
    Column("content", Text, nullable=False),
    Column("attached_at", UtcDateTime, nullable=False),
)

# --- Nudge-fired facts (issue #113, Phase 4) ----------------------------------
#
# At most one row per ``(lease_id, epoch)`` — the durable guard
# ``_advance_exited_worker`` (``runner/loop/steps.py``) consults before ever resuming
# a worker session to nudge it about a ``produces:`` name no git commit or attachment
# covers (``bzh:invariant-checker`` — "at most one nudge per (lease, epoch)"),
# idempotent by ``record_nudge_fired``'s own check-then-insert, not a DB constraint
# (``bzh:sql-portable``), mirroring ``usage_facts``. Written BEFORE the resume it
# guards, inverting this store's usual resume-then-record pairing (``lease_spawns``), so
# "at most one nudge" holds across a crash at either point — the crash-sweep window
# ``nudge.after-fired-fact.before-resume`` is what exercises it.

nudge_facts = Table(
    "nudge_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("nudged_at", UtcDateTime, nullable=False),
)

# --- Check results + the checks-ran guard (issue #114) -------------------------
#
# The runner runs a node's ``checks:`` at worker exit, before the judgement is elicited,
# and records each command's outcome here as a durable fact (``bzh:facts-not-status``) so
# a runner kill between check-run and judgement resumes at the right point without
# re-running or losing results — modeled on ``nudge_facts``/``attachments``.
#
# ``check_results`` holds one row per check command per ``(lease_id, epoch)``, append-only.
# ``chunk_id``/``node_id`` are carried off the lease at run time (denormalized, not joined
# back). ``output_tail`` is the bounded evidence — deliberately runner-local, and it never
# rides the wire (issue #114 [MF3]).
#
# ``checks_ran`` is the guard marker: at most one row per ``(lease_id, epoch)``, written
# AFTER the result rows (so ``check_results`` rows are durable before the marker is), and
# ONLY when the node declares a non-empty ``checks:`` (a node with no checks writes neither
# rows nor marker). On recovery the marker gates re-run: unset ⇒ re-run all (latest-wins,
# safe); set ⇒ read the recorded results back and judge. This makes execution at-least-once
# and the recorded results exactly-once — the shape ``nudge_facts`` guarantees. The
# invariant ``runner:checks-recorded-when-marked`` holds precisely because the marker is
# written last and only for non-empty checks: a marker implies its result rows exist.
#
# The re-run key is ``(lease_id, epoch)`` and never anything stable across a node re-entry:
# keying on ``(chunk, node)`` would wedge every retry on a stale red result. A verdict-less
# retry, a ``requires_checks`` gate-fire, and a node re-entry each mint a new
# ``(lease, epoch)`` and re-run; the only same-key re-drives are the hub-unreachable re-tick
# and the produces-nudge, neither of which authors new tree content.

check_results = Table(
    "check_results",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("command", Text, nullable=False),
    Column("passed", Boolean, nullable=False),
    Column("output_tail", Text, nullable=False),
    Column("ran_at", UtcDateTime, nullable=False),
)

checks_ran = Table(
    "checks_ran",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("ran_at", UtcDateTime, nullable=False),
)

# --- SSO federation jti replay cache (issue #95, decision D4) ---------------
#
# The single-use guard a hub-signed JWT's `jti` claim is checked against
# (`runner/auth/federation.py`): store-backed rather than in-memory so the guarantee
# survives a runner restart within the JWT's own short lifetime. The `jti` primary key
# alone is the single-use guarantee, enforced by the store — crash-correctness position:
# `runner/auth/jti_cache.py`. `expires_at` mirrors the claim's own `exp`, so a prune can
# drop rows safely past it.

jwt_jti_seen = Table(
    "jwt_jti_seen",
    metadata,
    Column("jti", String, primary_key=True),
    Column("aud", String, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
)

# --- Git-commit declarations (a worker's explicit git-commit artifact — issue #143, Phase 3) -
#
# A worker's explicit git-commit declaration, authorized by the lease's own capability
# token (``lease_tokens``) — a structural sibling of ``attachments`` above for the
# ``git_commit`` artifact kind: append-only, latest-wins-per-``(lease_id, repo)``
# (``bzh:facts-not-status`` — a chunk may span multiple repos, so the natural key is per
# repo, not per lease). ``chunk_id``/``node_id``/``epoch`` are denormalized off the lease
# at declare time, mirroring ``attachments``.

git_commit_declarations = Table(
    "git_commit_declarations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("environment_id", String, nullable=False),
    Column("repo", String, nullable=False),
    Column("branch", String, nullable=False),
    Column("commit", String, nullable=False),
    Column("declared_at", UtcDateTime, nullable=False),
)

# --- Session preamble facts (what standing prose a session was last sent — issue #149) --
#
# A resumed spawn re-sends the whole three-layer preamble today, including the two
# *standing* layers the session already holds. This table is the comparison key that lets
# the renderer skip the unchanged ones and announce the changed ones: one row per spawn
# recording the sha256 of layer 1 (the blizzard preamble) and layer 2 (the operator's
# workspace prompt) as that spawn resolved them.
#
# Digests, not the prose (``canon:one-owner``): the operator's text lives in
# ``workspace_prompt`` above and in config, and this table is never a second copy of it.
#
# Append-only (``bzh:facts-not-status``): the newest row for a session is the answer, so
# the read carries an explicit total ``order_by(id.desc())`` — a newest-row read with no
# ordering happens to work on sqlite and is undefined on postgres (``bzh:sql-portable``).
# No index: no runner facts table declares one, and a session's row count is its spawn
# count.
#
# Keyed on the SESSION, not the lease: the harness session is what already holds the
# earlier prose, and it outlives the per-attempt lease (pinned by
# tests/test_runner_store.py::test_session_preamble_fingerprint_is_scoped_per_session).
# A session with no row reads back ``None`` and renders in full — the safe direction, and
# why no data migration is owed.

session_preamble_facts = Table(
    "session_preamble_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String, nullable=False),
    Column("blizzard_digest", String, nullable=False),  # sha256 of the resolved layer 1
    Column("workspace_digest", String, nullable=False),  # sha256 of the resolved layer 2
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- External subscription usage samples (the harness's own rate-limit windows — issue #218) -
#
# One append-only row per tick's sampling *attempt*, never an upserted "last sampled"
# column: the cadence anchor (:meth:`IReadRunnerStore.last_external_usage_attempt_at`) is
# derived as ``max(sampled_at)`` over this table, following the facts-only pattern every
# other table in this module uses (``bzh:facts-not-status``). ``payload`` is the JSON-serialized
# :class:`~blizzard.runner.harness.external_usage.ExternalSubscriptionUsageSnapshot` the
# adapter returned, or NULL when that attempt produced nothing (no subscription concept, an
# unreachable/expired credential, anything — see
# :meth:`~blizzard.runner.harness.adapter.IHarnessAdapter.sample_external_subscription_usage`).
# A NULL-payload row still counts as an attempt for cadence purposes: the tick does not retry
# early just because the harness had nothing to report last time.

external_usage_samples = Table(
    "external_usage_samples",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sampled_at", UtcDateTime, nullable=False),
    Column("payload", Text, nullable=True),  # NULL = this attempt sampled nothing
)
