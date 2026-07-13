"""The runner store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``): the machine-local fast path
(D-023/D-028) — leases with their pid + process-start-time, chunk->env bindings,
and the store-and-forward outbound buffer. Timestamps come from the injected clock,
never a ``server_default`` (``bzh:injected-clock``); portable-SQL surface only
(``bzh:sql-portable``).

Walking-skeleton (P6) subset: the loop mints a lease, binds an environment, and
buffers each hub-bound fact for the flusher. Heartbeat / verdict / open-ask tables
are the runner-track builder's to add as the loop grows — the seam is the same
facts-only pattern.
"""

from __future__ import annotations

from sqlalchemy import (
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
# fact always precedes the transitions minted under it (D-044 made structural). A
# semantic rejection still advances the ack — rejection is an outcome, not a
# delivery failure. ``acked_at`` NULL means still pending.

outbound_buffer = Table(
    "outbound_buffer",
    metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # per-runner monotonic (D-069)
    Column("kind", String, nullable=False),  # lease.minted | transition.recorded | ...
    Column("chunk_id", String, nullable=True),  # the correlated chunk, when the fact has one
    Column("payload", Text, nullable=False),  # the JSON body posted to the matching hub route
    Column("created_at", DateTime, nullable=False),
    Column("acked_at", DateTime, nullable=True),  # NULL = pending; set when the hub acks the seq
)
