"""The runner store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``). Timestamps come from the injected
clock, never a ``server_default`` (``bzh:injected-clock``); portable-SQL surface only
(``bzh:sql-portable``)."""

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
# Pid alone is ambiguous across reuse, so liveness keys on (pid, process_start_time).

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
# One FIFO drain, so a lease fact always precedes the completion minted under it.

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
# Append-only: a lease's last heartbeat is ``max(beat_at)`` (``bzh:facts-not-status``).

heartbeats = Table(
    "heartbeats",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # the attempt the beat belongs to (BLIZZARD_LEASE_ID)
    Column("beat_at", UtcDateTime, nullable=False),  # injected-clock stamp of the tool call
)

# --- Lease node context (the node identity of each attempt) ------------------
# One row per lease, written at mint — a lease is one node-step attempt.

lease_context = Table(
    "lease_context",
    metadata,
    Column("lease_id", String, primary_key=True),  # 1:1 with leases.lease_id
    Column("chunk_id", String, nullable=False),
    Column("graph_id", String, nullable=False),
    Column("node_id", String, nullable=False),  # which node this attempt is at
    Column("node_name", String, nullable=False),
    Column("retries_max", Integer, nullable=False),  # the node's retry budget, from the envelope
    # The model/effort the session ACTUALLY ran under, never a freshly resolved
    # preference (issue #144). NULL means *unknown*, never a value.
    Column("session_name", String, nullable=True),
    Column("resolved_model", String, nullable=True),
    Column("resolved_effort", String, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Lease spawns (the spawn generation of each attempt — issue #13) ----------
# A lease outlives its sessions, so its newest `spawned_at` is the current generation.

lease_spawns = Table(
    "lease_spawns",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # the attempt this process was spawned for
    Column("spawned_at", UtcDateTime, nullable=False),  # injected-clock stamp of the spawn-return
)

# --- Lease closures (closed iff a closure fact exists — facts-not-status) -----
# `reason` separates a clean transition from a retry-consuming failure.

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

# --- Binding releases (released iff a release fact exists) -------------------
# Held env ids are `env_bindings` minus `binding_releases`.

binding_releases = Table(
    "binding_releases",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, nullable=False),
    Column("environment_id", String, nullable=False),
    Column("released_at", UtcDateTime, nullable=False),
)

# --- Asks (the worker's local open-ask fact) ---------------------------------
# Recorded before the worker exits, so it is durable by the time the process ends.

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
# Parked while a park_fact references a lease with no later park_resume.

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

# --- Pause park / resume (dormancy on an operator pause — issue #46) ---------
# A separate table pair: one NULL ``question_id`` would poison a NOT IN subquery.

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
# Resumes in place under the unchanged lease/epoch/session, consuming no retry budget.

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
# Absence, paired with a dead pid, is how startup tells a crash from a clean exit.

session_ends = Table(
    "session_ends",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # BLIZZARD_LEASE_ID the SessionEnd hook inherited
    Column("ended_at", UtcDateTime, nullable=False),  # injected-clock stamp of the session's exit
)

# --- Hub control mirror (the declarative pause brake read on PULL) -----------
# Mirrored so the last-known directive holds while the hub is unreachable.

hub_control = Table(
    "hub_control",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("paused", Boolean, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Local pause facts (the runner's own brake — issue #43) -------------------
# Appends rather than upserts; effective paused is the OR with ``hub_control``.

local_pause_facts = Table(
    "local_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, nullable=False),
    Column("paused", Boolean, nullable=False),  # locally paused derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# --- Workspace prompt override (runtime-settable spawn preamble — issue #17) --
# A present row, empty ``prompt`` included, wins over config; no row falls back to it.

workspace_prompt = Table(
    "workspace_prompt",
    metadata,
    Column("workspace_id", String, primary_key=True),
    Column("prompt", Text, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Daemon liveness (when the runner was last known alive — issue #13) -------
# The crash-time reference recovery measures staleness against, not `now - heartbeat`.

daemon_liveness = Table(
    "daemon_liveness",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("alive_at", UtcDateTime, nullable=False),  # injected-clock stamp of the newest tick
)

# --- Takeovers (the operator's session over a parked chunk — issue #52) -------
# Recorded **before** any kill, so no later tick can race the human for the chunk.

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

# --- Requeues (the explicit hand-back after a human hold — issue #53) --------
# Pending while no later lease was minted, so the next fresh spawn consumes it.

requeues = Table(
    "requeues",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, nullable=False),
    Column("requeued_at", UtcDateTime, nullable=False),  # supersedes an earlier escalation
)

# --- Usage facts (cost/token telemetry per invocation — issue #58) -----------
# Keyed ``(lease_id, generation, kind)``, so a replay writes nothing twice.

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

# --- Route capability tokens (the runner's stash — issue #84a) ---------------
# Only the *current* plaintext, one row per chunk: a fresh claim overwrites the prior.

route_tokens = Table(
    "route_tokens",
    metadata,
    Column("chunk_id", String, primary_key=True),
    Column("token", Text, nullable=False),
    Column("acquired_at", UtcDateTime, nullable=False),
)

# --- Lease capability tokens (issue #113, Phase 1) ---------------------------
# Only the sha256 hash is stored; the plaintext rides the spawn env, never persisted.

lease_tokens = Table(
    "lease_tokens",
    metadata,
    Column("lease_id", String, primary_key=True),
    Column("token_hash", Text, nullable=False),
    Column("minted_at", UtcDateTime, nullable=False),
)

# --- Attachments (a worker's explicit artifact submission — issue #113) ------
# Append-only, latest-wins-per-``(lease_id, name)``, so a re-submit is a correction.

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

# --- Nudge-fired facts (issue #113, Phase 4) ---------------------------------
# Written BEFORE the resume it guards, so "at most one nudge" survives a crash.

nudge_facts = Table(
    "nudge_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("nudged_at", UtcDateTime, nullable=False),
)

# --- Check results + the checks-ran guard (issue #114) -----------------------
# ``checks_ran`` is written last: a marker implies its result rows exist.

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

# --- SSO federation jti replay cache (issue #95, decision D4) ----------------
# The `jti` primary key alone is the single-use guarantee, enforced by the store.

jwt_jti_seen = Table(
    "jwt_jti_seen",
    metadata,
    Column("jti", String, primary_key=True),
    Column("aud", String, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
)

# --- Git-commit declarations (issue #143, Phase 3) ---------------------------
# Latest-wins-per-``(lease_id, repo)``: a chunk may span multiple repos.

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

# --- Session preamble facts (the standing prose last sent — issue #149) ------
# Digests, not the prose (``canon:one-owner``); keyed on the SESSION, not the lease.

session_preamble_facts = Table(
    "session_preamble_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String, nullable=False),
    Column("blizzard_digest", String, nullable=False),  # sha256 of the resolved layer 1
    Column("workspace_digest", String, nullable=False),  # sha256 of the resolved layer 2
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- External subscription usage samples (issue #218) ------------------------
# One row per sampling *attempt*: a NULL payload still counts toward the cadence.

external_usage_samples = Table(
    "external_usage_samples",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sampled_at", UtcDateTime, nullable=False),
    Column("payload", Text, nullable=True),  # NULL = this attempt sampled nothing
)

# --- Transcript segments (the segment ledger — issue #246, D2) ---------------
# Mutable, like `leases`, not append-only; keyed `(chunk_id, node_id, epoch, generation)`.

transcript_segments = Table(
    "transcript_segments",
    metadata,
    Column("segment_id", String, primary_key=True),  # seg_<ulid>
    Column("chunk_id", String, nullable=False, index=True),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("generation", Integer, nullable=False),  # this lease's spawn ordinal (1 = initial spawn)
    Column("lease_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("cursor", String, nullable=True),  # opaque TranscriptPosition.token; NULL = unread from the start
    Column("shipped_bytes", Integer, nullable=False),
    # Also this segment's next `turn_range_start` (blizzard#247's wire key) — turn indices
    # are segment-relative and gapless, so the running count doubles as the next offset.
    Column("shipped_turns", Integer, nullable=False),
    # A static per-harness constant, not something reading is needed to learn — stamped with
    # the source seam's "never ran" sentinel at spawn, so a closure always has one to declare.
    Column("normalizer_version", String, nullable=False),
    Column("harness_version", String, nullable=True),
    # Two fields, not one (review F1): `truncated_reason` never latches; `shipping_stopped_reason` does.
    Column("truncated_reason", String, nullable=True),  # NULL = no record ever shrunk
    Column("shipping_stopped_reason", String, nullable=True),  # NULL = still shipping (D4)
    Column("finalized_at", UtcDateTime, nullable=True),  # NULL = still open; set by step close
    Column("stamped_at", UtcDateTime, nullable=False),
)

# --- Transcript outbound buffer (the lane's own store-and-forward — D3) ------
# A second FIFO drain, its own sequence, structurally independent of `outbound_buffer`'s.

transcript_outbound_buffer = Table(
    "transcript_outbound_buffer",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # per-runner monotonic, own sequence
    Column("segment_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    # Mirrors the payload's own `final` flag, so ack-time keep-vs-delete needs no JSON read.
    Column("final", Boolean, nullable=False),
    # A blizzard.wire.transcript_segment.TranscriptSegmentRecord's fields, minus `seq`
    # (this row's own PK) and `runner_id` (batch-level, added at drain time).
    Column("payload", Text, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    # NULL = pending. An acked non-final row is deleted, never reaching this state; an
    # acked final row IS marked here — its continued presence is the exactly-once receipt.
    Column("acked_at", UtcDateTime, nullable=True),
    # Real SQLite AUTOINCREMENT (review F1): a bare `INTEGER PRIMARY KEY` would reuse a
    # pruned row's rowid, reissuing a seq the hub already marked applied.
    sqlite_autoincrement=True,
)
