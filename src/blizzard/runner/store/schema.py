"""The runner store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``): the machine-local fast path
(D-023/D-028) — leases with their pid + process-start-time, chunk->env bindings,
and the store-and-forward outbound buffer. Timestamps come from the injected clock,
never a ``server_default`` (``bzh:injected-clock``); portable-SQL surface only
(``bzh:sql-portable``).

The loop mints a lease, binds an environment, buffers each hub-bound fact for the
flusher (store-and-forward, D-069), records a heartbeat per worker tool call
(progress detection, design/runner/loop.md), and — for the ask/answer protocol
([ask-answer.md]) — records the local open-ask fact and the chunk's park/resume
around it. All the same facts-only pattern.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

# --- Leases (the machine's execution right now — D-023/D-035) ---------------
#
# The lease carries the pid + process start time, recorded by the spawn wrapper
# from inside the child (D-092): pid alone is ambiguous across reuse, so REAP
# keys on (pid, process_start_time) — the P6 liveness signal, heartbeats being P7.

leases = Table(
    "leases",
    metadata,
    Column("lease_id", String, primary_key=True),  # lease_<ulid>
    Column("chunk_id", String, nullable=False),  # the chunk this lease attempt is for
    Column("epoch", Integer, nullable=False),  # incrementing fence, reported to the hub (D-044)
    Column("runner_id", String, nullable=False),
    Column("pid", Integer, nullable=True),  # filled at spawn-return (D-092)
    Column("process_start_time", String, nullable=True),  # stable across pid reuse; REAP keys on it
    Column("session_id", String, nullable=True),  # harness-assigned, recorded at spawn-return
    Column("created_at", DateTime, nullable=False),
)

# --- Environment bindings (chunk -> env ids, from the provider — D-021/D-062) -

env_bindings = Table(
    "env_bindings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, nullable=False),
    Column("environment_id", String, nullable=False),  # opaque provider id
    Column("workdir", String, nullable=False),  # provider-returned working directory (D-063)
    Column("bound_at", DateTime, nullable=False),
)

# --- Outbound buffer (store-and-forward, per-runner monotonic seq — D-069) ---
#
# Every hub-bound fact is written here at mint, stamped with a monotonic sequence,
# even when the hub is reachable: one flusher drains it in FIFO order, so a lease
# fact always precedes the completion minted under it (D-044 made structural). A
# semantic rejection still advances the ack — rejection is an outcome, not a
# delivery failure. ``acked_at`` NULL means still pending. ``lease_id`` correlates a
# buffered fact back to its attempt: the flusher drives a completion's apply-response
# (closure + next-node spawn) against the lease it names, and ADVANCE skips a lease
# whose completion is already buffered so it is never elicited twice.

outbound_buffer = Table(
    "outbound_buffer",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # per-runner monotonic (D-069)
    Column("kind", String, nullable=False),  # lease.minted | completion.submitted | escalation.recorded
    Column("chunk_id", String, nullable=True),  # the correlated chunk, when the fact has one
    Column("lease_id", String, nullable=True),  # the correlated attempt, when the fact has one
    Column("payload", Text, nullable=False),  # the JSON body posted to the matching hub route
    Column("created_at", DateTime, nullable=False),
    Column("acked_at", DateTime, nullable=True),  # NULL = pending; set when the hub acks the seq
)

# --- Heartbeats (progress detection, machine-local — never leaves the box) ----
#
# A worker heartbeats as a side effect of working: every tool call fires a
# ``PostToolUse`` hook that runs ``blizzard runner heartbeat``, which posts to the
# runner's local API and appends a row here (design/runner/loop.md, design/
# harness-adapters.md). Append-only (``bzh:facts-not-status``): the *last* heartbeat
# for a lease is ``max(beat_at)``. REAP reads it to catch a stalled-but-alive worker
# — one whose pid is live but whose heartbeat has gone stale. The heartbeat never
# travels to the hub (domain/events.md): ``stalled`` is a runner-local derivation.

heartbeats = Table(
    "heartbeats",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # the attempt the beat belongs to (BLIZZARD_LEASE_ID)
    Column("beat_at", DateTime, nullable=False),  # injected-clock stamp of the tool call
)

# --- Lease node context (the node identity of each attempt — 0002's leases lacks it) -
#
# 0002's `leases` table is frozen; the node a lease attempts (and the retry budget
# the node carries) is the one fact the reconciliation loop needs that it does not
# hold. Written once per lease at mint. Append-only, one row per lease (D-082 — a
# lease is one node-step attempt).

lease_context = Table(
    "lease_context",
    metadata,
    Column("lease_id", String, primary_key=True),  # 1:1 with leases.lease_id
    Column("chunk_id", String, nullable=False),
    Column("graph_id", String, nullable=False),
    Column("node_id", String, nullable=False),  # which node this attempt is at
    Column("node_name", String, nullable=False),
    Column("retries_max", Integer, nullable=False),  # the node's retry budget, from the envelope
    Column("recorded_at", DateTime, nullable=False),
)

# --- Lease closures (a lease is closed iff a closure fact exists — facts-not-status) -
#
# Append-only: an active lease is one with no closure. `reason` distinguishes a
# clean node transition (`transitioned`) from an execution-attempt failure that
# counts against the node's retries (`reaped`, `failed`) and a retries-exhausted
# escalation (`escalated`, D-078/D-009).

lease_closures = Table(
    "lease_closures",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("reason", String, nullable=False),  # transitioned | reaped | failed | escalated
    Column("closed_at", DateTime, nullable=False),
)

# --- Binding releases (a binding is released iff a release fact exists — D-062/D-083) -
#
# An env binding rides the chunk's tenure; it is freed only when the chunk leaves
# the runner (terminal, stop, detach). `release()` is a no-op mark at the provider,
# so the release truth lives here as a runner-store fact. Held env ids are
# `env_bindings` minus `binding_releases`.

binding_releases = Table(
    "binding_releases",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, nullable=False),
    Column("environment_id", String, nullable=False),
    Column("released_at", DateTime, nullable=False),
)

# --- Asks (the worker's local open-ask fact — [ask-answer.md]) ---------------
#
# ``blizzard runner ask`` hits the runner's local API before the worker exits, so
# the ask is durable by the time the process ends — that is how ADVANCE tells "parked
# on a question" apart from "died without a verdict" (D-009). The runner mints the
# ``question_id`` here so it can poll the hub for the answer by it. An ask is
# *unforwarded* (awaiting park) until a park_fact references its question_id.

asks = Table(
    "asks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # BLIZZARD_LEASE_ID the worker inherited
    Column("chunk_id", String, nullable=False),
    Column("question_id", String, nullable=False),  # qn_<ulid>, runner-minted (D-075)
    Column("question", Text, nullable=False),
    Column("options", Text, nullable=False),  # JSON list[str] (may be empty)
    Column("session_id", String, nullable=True),  # the session to resume around the answer
    Column("asked_at", DateTime, nullable=False),
)

# --- Park / resume (the chunk's dormancy on a question — [ask-answer.md]) ----
#
# A lease is *parked* while a park_fact references it with no later park_resume: the
# worker asked and exited (ask-and-exit), so there is no live worker — REAP must not
# count the park as a stall, and ADVANCE must not elicit a verdict. The answer's
# arrival records a park_resume, the dormant session is resumed, and the lease is live
# again (a fresh pid recorded via record_spawn).

park_facts = Table(
    "park_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("question_id", String, nullable=False),  # the ask this park is on
    Column("parked_at", DateTime, nullable=False),
)

park_resumes = Table(
    "park_resumes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("question_id", String, nullable=False),
    Column("resumed_at", DateTime, nullable=False),
)

# --- Resume intent (the graceful-restart resume marker — D-082) --------------
#
# A graceful ``blizzard-runner`` shutdown (SIGTERM: ``systemctl restart``/stop) marks
# every active, non-parked, session-bearing lease with a resume-intent *before* the
# daemon exits, then the startup RESUME step routes each marked lease to a same-lease
# resume — kill any survivor, then resume the session in place under the **unchanged**
# ``lease_id``/``epoch``/``session_id`` (only ``pid``/``process_start_time`` are
# rewritten). This is the fourth sibling of the resume family (spawn / judgement /
# answer, D-082): it is explicitly not a retry (new lease/epoch/session), so it consumes
# no retry budget (D-078). An ungraceful ``kill -9`` writes no intent, so a crashed
# worker still routes to today's reap/requeue-fresh — the scope boundary is exactly
# "did the daemon get to run shutdown code".
#
# Facts-only (``bzh:facts-not-status``), mirroring park/park_resume: an intent is *open*
# while a ``resume_intents`` row has no ``resume_clears`` for the same lease at or after
# it — the RESUME step records a clear once it resumes (or abandons) the lease, and a
# later graceful restart of a still-in-flight lease marks it afresh above that clear.

resume_intents = Table(
    "resume_intents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("marked_at", DateTime, nullable=False),
)

resume_clears = Table(
    "resume_clears",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("cleared_at", DateTime, nullable=False),
)

# --- Hub control mirror (the declarative pause brake read on PULL — D-043/D-012) --
#
# The fleet operator's pause brake lives at the hub (registry ``paused``, D-043); the
# runner reads it on its outbound PULL and mirrors it here, then FILL adheres — paused
# stops new claims, in-flight chunks run on ([loop.md]). Mirroring it in the store keeps
# the read a machine-local, crash-safe fact: FILL never calls the hub itself, and the
# last-known directive holds while the hub is unreachable (D-012). One upserted row per
# runner; ``paused`` is the value, ``updated_at`` when PULL last refreshed it.

hub_control = Table(
    "hub_control",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("paused", Boolean, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
