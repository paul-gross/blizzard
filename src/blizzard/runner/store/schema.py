"""The runner store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``): the machine-local fast path
 — leases with their pid + process-start-time, chunk->env bindings,
and the store-and-forward outbound buffer. Timestamps come from the injected clock,
never a ``server_default`` (``bzh:injected-clock``); portable-SQL surface only
(``bzh:sql-portable``).

The loop mints a lease, binds an environment, buffers each hub-bound fact for the
flusher (store-and-forward), records a heartbeat per worker tool call
(progress detection), and — for the ask/answer protocol — records the local
open-ask fact and the chunk's park/resume around it. All the same facts-only
pattern.
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
# The lease carries the pid + process start time, recorded by the spawn wrapper
# from inside the child: pid alone is ambiguous across reuse, so REAP
# keys on (pid, process_start_time) — the P6 liveness signal, heartbeats being P7.

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
# the FIFO drain, not a runtime check. A
# semantic rejection still advances the ack — rejection is an outcome, not a
# delivery failure. ``acked_at`` NULL means still pending. ``lease_id`` correlates a
# buffered fact back to its attempt: the flusher drives a completion's apply-response
# (closure + next-node spawn) against the lease it names, and ADVANCE skips a lease
# whose completion is already buffered so it is never elicited twice.

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
# A worker heartbeats as a side effect of working: every tool call fires a
# ``PostToolUse`` hook that runs ``blizzard runner heartbeat``, which posts to the
# runner's local API and appends a row here. Append-only (``bzh:facts-not-status``):
# the *last* heartbeat for a lease is ``max(beat_at)``. REAP reads it to catch a
# stalled-but-alive worker — one whose pid is live but whose heartbeat has gone
# stale. The heartbeat never travels to the hub: ``stalled`` is a runner-local
# derivation.

heartbeats = Table(
    "heartbeats",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # the attempt the beat belongs to (BLIZZARD_LEASE_ID)
    Column("beat_at", UtcDateTime, nullable=False),  # injected-clock stamp of the tool call
)

# --- Lease node context (the node identity of each attempt — the walking-skeleton revision's leases lacks it) -
#
# The walking-skeleton revision's `leases` table is frozen; the node a lease attempts (and the retry budget
# the node carries) is the one fact the reconciliation loop needs that it does not
# hold. Written once per lease at mint. Append-only, one row per lease — a
# lease is one node-step attempt.

lease_context = Table(
    "lease_context",
    metadata,
    Column("lease_id", String, primary_key=True),  # 1:1 with leases.lease_id
    Column("chunk_id", String, nullable=False),
    Column("graph_id", String, nullable=False),
    Column("node_id", String, nullable=False),  # which node this attempt is at
    Column("node_name", String, nullable=False),
    Column("retries_max", Integer, nullable=False),  # the node's retry budget, from the envelope
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- Lease spawns (the spawn generation of each attempt — issue #13) ----------
#
# The walking-skeleton revision's `leases` is frozen and `record_spawn` rewrites its pid/session in place, so
# the lease alone cannot say *when* its current process was spawned. A lease outlives
# its sessions — the ask/answer and resume paths re-spawn under the same lease_id and
# session_id (`_resume_if_answered`, `_resume_in_place`) — so a per-lease fact that is
# true "forever after" cannot be read as true "of the process running now".
#
# Append-only, one row per spawn: the newest `spawned_at` for a lease is its current
# spawn generation. Startup crash-recovery scopes the session-end check to it, so a
# session-end left by an *earlier* session of the same lease no longer reads as "this
# process declared done" and permanently suppresses its resume.

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
# the runner (terminal, stop, detach). `release()` is a no-op mark at the provider,
# so the release truth lives here as a runner-store fact. Held env ids are
# `env_bindings` minus `binding_releases`.

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
# ``blizzard runner ask`` hits the runner's local API before the worker exits, so
# the ask is durable by the time the process ends — that is how ADVANCE tells "parked
# on a question" apart from "died without a verdict". The runner mints the
# ``question_id`` here so it can poll the hub for the answer by it. An ask is
# *unforwarded* (awaiting park) until a park_fact references its question_id.

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
# A deliberate SEPARATE table pair from park_facts/park_resumes above, not a reshape:
# ``unforwarded_ask`` (below) reads ``asks.c.question_id.not_in(select(park_facts.c.
# question_id))`` — a nullable ``question_id`` on that table would make SQL's
# ``x NOT IN (subquery containing NULL)`` evaluate to NULL for *every* row, silently
# breaking ask-and-exit fleet-wide with a green gate. A pause has no natural key
# (unlike an ask's fresh ``question_id`` per ask), so mirroring the same table with a
# nullable question id is unsafe by construction; a separate table makes it
# unreachable. See ``pause_park_resumes`` below for the corresponding non-key-based
# open predicate.

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
# A restart marks every active, non-parked, session-bearing lease with a resume-intent, then
# the startup RESUME step routes each marked lease to a same-lease resume — kill any survivor,
# then resume the session in place under the **unchanged** ``lease_id``/``epoch``/``session_id``
# (only ``pid``/``process_start_time`` are rewritten). This is the fourth sibling of the resume
# family (spawn / judgement / answer): it is explicitly not a retry (new lease/epoch/
# session), so it consumes no retry budget.
#
# Two paths write the intent. A **graceful** shutdown (SIGTERM: ``systemctl restart``/stop)
# marks *before* the daemon exits (#12). An ungraceful ``kill -9`` / OOM / reboot never runs
# shutdown code, so ``host``'s **startup crash-recovery** scan marks it instead (#13,
# ``mark_crash_resume_intents``) — for a lease whose worker is gone with no recorded session-end
# and a non-stale heartbeat, i.e. killed mid-work rather than done or already stalled. The
# RESUME step is indifferent to which path marked it.
#
# Facts-only (``bzh:facts-not-status``), mirroring park/park_resume: an intent is *open*
# while a ``resume_intents`` row has no ``resume_clears`` for the same lease at or after
# it — the RESUME step records a clear once it resumes (or abandons) the lease, and a
# later restart of a still-in-flight lease marks it afresh above that clear.

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
# The graceful marker (above) fires *before* the daemon exits; an ungraceful ``kill -9``
# / OOM / reboot never runs shutdown code, so startup crash-recovery cannot rely on a
# marker at all. This table is the signal it *can* rely on: the Claude Code ``SessionEnd``
# hook posts ``blizzard runner session-end`` when a worker's session exits naturally, so a
# row here means the worker **declared done** (exit-is-done). A worker killed
# mid-work never runs the hook, so it has no row — and that *absence*, paired with a dead
# pid, is how startup tells a crash to resume (:func:`mark_crash_resume_intents`) from a
# clean exit ADVANCE should judge. Append-only, machine-local (never travels to the hub),
# mirroring ``heartbeats`` (``bzh:facts-not-status``): a lease "ended" iff a row exists.

session_ends = Table(
    "session_ends",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),  # BLIZZARD_LEASE_ID the SessionEnd hook inherited
    Column("ended_at", UtcDateTime, nullable=False),  # injected-clock stamp of the session's exit
)

# --- Hub control mirror (the declarative pause brake read on PULL) -----------
#
# The fleet operator's pause brake lives at the hub (registry ``paused``); the
# runner reads it on its outbound PULL and mirrors it here, then FILL adheres — paused
# stops new claims, in-flight chunks run on. Mirroring it in the store keeps
# the read a machine-local, crash-safe fact: FILL never calls the hub itself, and the
# last-known directive holds while the hub is unreachable. One upserted row per
# runner; ``paused`` is the value, ``updated_at`` when PULL last refreshed it.

hub_control = Table(
    "hub_control",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("paused", Boolean, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Local pause facts (the runner's own brake — issue #43) -------------------
#
# The runner's half of the pause control (``PATCH /runner``, the same declarative-brake
# pattern applied locally): the
# operator tells *this* runner to stop claiming, and it adheres without the hub knowing
# or being reachable — the operator contract's standing requirement. Distinct
# from ``hub_control`` above in both concept and shape: that mirrors a hub-owned value,
# so it upserts; this is a locally-minted fact, so pause/start facts **append** and the
# flag derives from the newest, exactly like the hub's own
# ``runner_pause_facts``. Effective paused is the OR of the two — FILL adheres to either.
# ``set_by`` records who flipped it, on the fact.

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
# The runner prepends a standing workspace prompt to every worker spawn. Its static
# source is config (``blizzard-runner.toml``, loaded at ``host`` startup); this table
# is the *runtime* override the local API writes (``PUT /api/workspace-prompt``), so a
# replacement takes effect on subsequent spawns with no restart. One upserted row per
# workspace (the runner is single-workspace), mirroring ``hub_control``'s shape.
# A present row (including an empty ``prompt``) is a deliberate override that wins over
# the static config; no row means "never overridden — fall back to config".

workspace_prompt = Table(
    "workspace_prompt",
    metadata,
    Column("workspace_id", String, primary_key=True),
    Column("prompt", Text, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Daemon liveness (when the runner was last known alive — issue #13) -------
#
# The crash-time reference startup recovery classifies against. A worker's staleness
# is "was it still working *when the daemon died*" — but a restart only has the clock
# at recovery, and `now - last_heartbeat` silently measures `downtime + idle-at-crash`.
# An outage longer than the staleness threshold would then read every in-flight lease
# as stalled, defeating the reboot case #13 exists for.
#
# The tick stamps this each pass (~30s), so after a crash the last row is when the
# daemon was last alive — crash time, accurate to one tick. One upserted row per
# runner, mirroring ``hub_control``'s shape. No row means "never ticked": recovery
# falls back to the wall clock, which is the pre-#13 reading and only reachable on a
# store that has never run a tick (so it has no in-flight leases to misjudge).

daemon_liveness = Table(
    "daemon_liveness",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("alive_at", UtcDateTime, nullable=False),  # injected-clock stamp of the newest tick
)

# --- Takeovers (the operator's interactive session over a parked chunk — issue #52) --
#
# Recorded by ``POST /chunks/{id}/takeovers`` **before** any kill and before the interactive
# command is returned, so no later tick can race the human for the chunk (facts-not-status):
# while a takeover is open, REAP and ADVANCE (judgement, ask-resume, pause-resume, the
# gate/hub-node poll) all skip the chunk. ``lease_id`` is the lease taken over, when one
# exists (a live worker, force-killed, or a dormant ask-parked lease); ``None`` for the
# needs_human and gate-parked shapes, whose lease already closed before the takeover. Mirrors
# ``asks``' natural-key openness (a fresh ``takeover_id`` per open, unlike a pause's key-less
# fact pair): a plain ``takeover_id NOT IN (select takeover_id from takeover_ends)`` is safe
# here, since a chunk cannot carry two simultaneously-open takeovers (the open check refuses
# a second one).
#
# ``fence_epoch`` is set only on a **forced** takeover of a live worker: the epoch this
# chunk's takeover fact reports to the hub via a ``lease.minted``-kind outbound fact, so the
# killed worker's in-flight completion is fenced as stale exactly like a reaped lease —
# without counting as an execution attempt (no ``lease_context`` row is written, so
# ``attempt_count`` — the retry budget — never sees it). ``None`` on a non-forced takeover of
# an already-dormant lease, which needs no fence: nothing live can submit late.
# :meth:`~blizzard.runner.store.repository.IReadRunnerStore.latest_epoch` folds this in
# alongside ``leases.epoch`` so a later real spawn never reuses an epoch this fence already
# reported to the hub.

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
# ``blizzard runner requeue <chunk-id>`` appends this fact to clear a chunk's local
# needs_human hold — whether the chunk is escalated outright or was escalated and is
# now held by an *ended* takeover; either way the underlying shape is the same closed-
# ``escalated`` lease with no later mint (``domain/requeue.py``), so one fact and one
# openness predicate cover both. Facts-only (``bzh:facts-not-status``): a requeue mark
# is *pending* while no later lease was minted for the chunk — the same "no later mint"
# openness :func:`open_escalation`-equivalent reads at the hub — so the next FILL's
# fresh spawn (an ordinary lease mint) both consumes this mark and, via its outbound
# ``lease.minted`` fact, supersedes the escalation at the hub. Distinct from the hub's
# own ``requeues`` table (``blizzard hub requeue``, a different verb that also releases
# the route so *any* runner may reclaim the chunk): this mark never leaves the runner
# and never touches the route.

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
# extracted — either straight off the harness's own result envelope (``parse_usage``) or,
# when no envelope survived a killed/reaped worker, summed off the raw session transcript
# with ``cost_usd`` left absent (``sum_transcript_usage``) — never fabricated
# (``bzh:facts-not-status``). Keyed on ``(lease_id, generation, kind)``: ``generation`` is
# this lease's spawn ordinal (``lease_spawns``' own counting, issue #13, reused rather than
# duplicated) — a resume within the same lease mints a new generation and so a genuinely
# new row, while a replay of the exact same invocation (a crash between this write and its
# outbound-buffer pairing, re-run by the next tick before the completion is buffered)
# finds the row already there and writes nothing twice (``record_usage``'s own check, not
# a DB constraint — the store stays portable-SQL, ``bzh:sql-portable``). ``cost_usd`` NULL
# is the envelope-less fallback's honest "unknown", read by a summing caller as a lower
# bound, never as zero.

usage_facts = Table(
    "usage_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lease_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("generation", Integer, nullable=False),  # this lease's spawn ordinal (1 = the initial spawn)
    Column("kind", String, nullable=False),  # spawn | resume | judge
    Column("model", String, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cache_read_tokens", Integer, nullable=False),
    Column("cache_create_tokens", Integer, nullable=False),
    Column("cost_usd", Float, nullable=True),  # None = no envelope for this invocation — never fabricated
    Column("recorded_at", UtcDateTime, nullable=False),
)
