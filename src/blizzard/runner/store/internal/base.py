"""Package-private infrastructure shared by every ``runner/store/internal/`` adapter:
the connection helper each concept adapter takes in place of a bare
``Engine``, and the fact-closure predicates more than one concept's rows share — a
predicate used by exactly one concept is defined at that concept's own adapter instead.

Every predicate is a builder over the store's scoped statements (:class:`RunnerStoreConnections`), so its subqueries
are confined to the bound runner as it is built: no runner-scoped statement is built at import time."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Connection, Row, Select, Table, and_, join
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.roles import domain_model
from blizzard.runner.leases.model import Lease, WorkRefStamp
from blizzard.runner.store.schema import (
    OUTBOX_LANE_OUTBOUND,
    OUTBOX_LANE_TRANSCRIPT,
    binding_releases,
    env_bindings,
    escalation_closures,
    fact_counters,
    lease_closures,
    lease_context,
    leases,
    outbound_buffer,
    outbox_counters,
    pause_park_resumes,
    pause_parks,
    resume_clears,
    resume_intents,
    transcript_outbound_buffer,
)
from blizzard.runner.store.scoped import RUNNER_COLUMN, RunnerStoreConnections

#: A fresh segment's placeholder, before its first pump read — restated rather than
#: imported (the store never depends on the harness seam). Shared by the leases and transcripts adapters.
NO_NORMALIZER_VERSION = ""


@domain_model
@dataclass(frozen=True)
class Unsuperseded:
    """A fact row stands while no superseding row exists — one correlated ``NOT EXISTS``.

    Correlated on the superseding row's own ordering column, an append sequence, an instant or an epoch, never
    a bare key ``NOT IN``: a re-mark above an earlier close reads as open again."""

    marker: Any
    conditions: tuple[Any, ...]

    def clause(self, store: RunnerStoreConnections) -> ColumnElement[bool]:
        """The ``NOT EXISTS``, its superseding rows confined to ``store``'s runner."""
        return ~store.exists(self.marker.table, *self.conditions)


@domain_model
@dataclass(frozen=True)
class Unclosed:
    """A row stands while no closing row names its id — a plain ``NOT IN``.

    Its key is a fresh ULID per open, so there is no re-open-under-the-same-key hazard for
    the correlated form above to guard against."""

    key: Any
    closers: Any

    def clause(self, store: RunnerStoreConnections) -> ColumnElement[bool]:
        """The ``NOT IN``, its closing rows confined to ``store``'s runner."""
        return self.key.not_in(store.select(self.closers.table, self.closers))


# A release closes only a bind appended before it; pinned by
# tests/test_pin_runner_store.py::test_a_same_instant_rebind_after_a_release_reads_as_held.
HELD_BINDING = Unsuperseded(
    binding_releases.c.id,
    (
        binding_releases.c.chunk_id == env_bindings.c.chunk_id,
        binding_releases.c.environment_id == env_bindings.c.environment_id,
        binding_releases.c.seq > env_bindings.c.seq,
    ),
)

# Pinned by tests/test_runner_restart_resume.py::test_remark_across_two_restarts_reopens_the_intent.
OPEN_INTENT = Unsuperseded(
    resume_clears.c.id,
    (
        resume_clears.c.lease_id == resume_intents.c.lease_id,
        resume_clears.c.cleared_at >= resume_intents.c.marked_at,
    ),
)

# A resume closes only a park appended before it, so a second pause under one lease is not masked by the first
# pause's resume, even at the same instant.
_UNRESUMED_PAUSE_PARK = Unsuperseded(
    pause_park_resumes.c.id,
    (
        pause_park_resumes.c.lease_id == pause_parks.c.lease_id,
        pause_park_resumes.c.seq > pause_parks.c.seq,
    ),
)

# `bzh:open-facts-declare-closure`: a hub-terminal chunk retires its lease (`lease_closures`),
# and a park on a closed lease stands no longer — no resume will ever follow it.
_PAUSE_PARK_LEASE_UNCLOSED = Unclosed(pause_parks.c.lease_id, lease_closures.c.lease_id)


def open_pause_park(store: RunnerStoreConnections) -> ColumnElement[bool]:
    """A pause park stands until its own resume, or until its lease closes."""
    return and_(_UNRESUMED_PAUSE_PARK.clause(store), _PAUSE_PARK_LEASE_UNCLOSED.clause(store))


def pause_parked_lease_ids(store: RunnerStoreConnections) -> Select[Any]:
    """The pause-park half of ask/park's ``parked_lease_ids`` union — shared so the ask
    adapter never reaches into a sibling adapter for it."""
    return store.select(pause_parks, pause_parks.c.lease_id).where(open_pause_park(store)).distinct()


# Correlated against ``open_escalations``'s own outer ``leases``/``lease_closures`` join.
_LATER_LEASE = leases.alias("later_escalation_leases")
LIVE_ESCALATION = Unsuperseded(
    _LATER_LEASE.c.lease_id,
    (_LATER_LEASE.c.chunk_id == leases.c.chunk_id, _LATER_LEASE.c.epoch > leases.c.epoch),
)

# Strict ``>``, not ``>=`` (#292) — pinned by
# tests/test_pin_runner_store.py::test_a_same_instant_escalation_closure_does_not_mask_its_escalation.
UNRESOLVED_ESCALATION = Unsuperseded(
    escalation_closures.c.id,
    (
        escalation_closures.c.chunk_id == lease_closures.c.chunk_id,
        escalation_closures.c.closed_at > lease_closures.c.closed_at,
    ),
)


def lease_select(store: RunnerStoreConnections) -> Select[Any]:
    """The lease+context join every lease read selects from — shared with the transcripts
    ledger's backfill read, which also joins a lease — both sides confined to ``store``'s runner."""
    return store.select(
        join(leases, lease_context, lease_context.c.lease_id == leases.c.lease_id),
        leases.c.lease_id,
        leases.c.chunk_id,
        leases.c.epoch,
        leases.c.pid,
        leases.c.process_start_time,
        leases.c.session_id,
        leases.c.harness_id,
        leases.c.pgid,
        leases.c.created_at,
        lease_context.c.graph_id,
        lease_context.c.node_id,
        lease_context.c.node_name,
        lease_context.c.retries_max,
        # The session stamps — selected on the shared join rather than a
        # second query, so every lease read carries them.
        lease_context.c.session_name,
        lease_context.c.resolved_model,
        lease_context.c.resolved_effort,
        lease_context.c.resolved_compaction_window,
        lease_context.c.graph_name,
        lease_context.c.work_refs,
    )


def encode_work_refs(refs: tuple[WorkRefStamp, ...] | None) -> str | None:
    """The ``work_refs`` column's JSON array; ``label`` rides only where one was rendered."""
    if refs is None:
        return None
    return json.dumps(
        [{"source": s.source, "ref": s.ref, **({"label": s.label} if s.label is not None else {})} for s in refs]
    )


def decode_work_refs(raw: str | None) -> tuple[WorkRefStamp, ...] | None:
    if raw is None:
        return None
    return tuple(WorkRefStamp(source=e["source"], ref=e["ref"], label=e.get("label")) for e in json.loads(raw))


def row_to_lease(r: Row[Any]) -> Lease:
    return Lease(
        lease_id=str(r.lease_id),
        chunk_id=str(r.chunk_id),
        graph_id=str(r.graph_id),
        node_id=str(r.node_id),
        node_name=str(r.node_name),
        epoch=int(r.epoch),
        retries_max=int(r.retries_max),
        created_at=r.created_at,
        session_name=r.session_name,
        resolved_model=r.resolved_model,
        resolved_effort=r.resolved_effort,
        resolved_compaction_window=r.resolved_compaction_window,
        graph_name=r.graph_name,
        work_refs=decode_work_refs(r.work_refs),
        pid=int(r.pid) if r.pid is not None else None,
        process_start_time=str(r.process_start_time) if r.process_start_time is not None else None,
        session_id=str(r.session_id) if r.session_id is not None else None,
        harness_id=str(r.harness_id) if r.harness_id is not None else None,
        pgid=int(r.pgid) if r.pgid is not None else None,
    )


#: Each outbox table's lane in ``outbox_counters``.
_OUTBOX_LANES: dict[Table, str] = {
    outbound_buffer: OUTBOX_LANE_OUTBOUND,
    transcript_outbound_buffer: OUTBOX_LANE_TRANSCRIPT,
}


def insert_outbox_row(store: RunnerStoreConnections, conn: Connection, table: Table, **values: Any) -> int:
    """Insert one row into an outbox table, ``outbound_buffer`` or ``transcript_outbound_buffer``, on ``conn`` — the
    caller's transaction — under the next ``seq`` of the bound runner's lane; answer that ``seq``.

    The one door every outbox write passes through: the lane's counter in ``outbox_counters`` advances in the same
    transaction as the row, so a rollback returns the seq, and a counter never moves backwards, so a pruned row's seq is
    never issued again. ``tests/test_layering.py`` fails an insert into either table that goes around it."""
    seq = _advance_counter(
        store, conn, outbox_counters, outbox_counters.c.lane == _OUTBOX_LANES[table], lane=_OUTBOX_LANES[table]
    )
    conn.execute(store.insert(table).values(seq=seq, **values))
    return seq


def insert_fact_row(store: RunnerStoreConnections, conn: Connection, table: Table, **values: Any) -> int:
    """Insert one row into a sequenced fact table on ``conn`` — the caller's transaction —
    under the next ``seq`` of the bound runner's fact counter; answer that ``seq``.

    The sequenced tables are ``env_bindings``, ``binding_releases``, ``pause_parks``, ``pause_park_resumes``,
    ``requeues`` and ``leases``: each closure between them — release closes bind, resume closes park, lease mint
    consumes requeue — is judged by ``seq``, never by comparing instants.

    The one door every write to those tables passes through: the counter advances in the same transaction as the row,
    so rows are numbered in the order they were appended and a rollback returns the seq. ``tests/test_layering.py``
    fails an insert into any of them that goes around it."""
    seq = _advance_counter(store, conn, fact_counters)
    conn.execute(store.insert(table).values(seq=seq, **values))
    return seq


def _advance_counter(
    store: RunnerStoreConnections,
    conn: Connection,
    counters: Table,
    where: ColumnElement[bool] | None = None,
    **key: Any,
) -> int:
    """Advance the bound runner's counter row in ``counters`` (the one ``where`` selects, created with ``key`` columns)
    and answer its new value. The advance takes the counter's row lock, so concurrent writers queue behind it; the first
    allocation creates the row, and a writer that loses that race advances the row the winner created."""
    advance = store.update(counters).values(last_seq=counters.c.last_seq + 1)
    read = store.select(counters, counters.c.last_seq)
    if where is not None:
        advance, read = advance.where(where), read.where(where)
    if not conn.execute(advance).rowcount:
        try:
            with conn.begin_nested():
                conn.execute(store.insert(counters).values(last_seq=1, **key))
        except IntegrityError:
            conn.execute(advance)
        else:
            return 1
    return conn.execute(read).scalar_one()


#: The tables a runner transfer carries through :func:`carry_outbox_rows` rather than its generic table loop.
CARRIED_OUTBOX_TABLES: frozenset[Table] = frozenset({*_OUTBOX_LANES, outbox_counters})


def carry_outbox_rows(
    source: RunnerStoreConnections,
    source_conn: Connection,
    destination: RunnerStoreConnections,
    destination_conn: Connection,
) -> None:
    """The outboxes' other door: copy the bound runner's outbox rows from ``source_conn`` into ``destination_conn``
    (the destination's transaction) under the ``seq``s they were issued, which the runner's hub already judges them by,
    and set each lane counter to the larger of the two stores', so it never moves backwards — even into a store that
    kept a larger one when the runner was removed from it. Both stores are bound to the moving runner."""
    for table in _OUTBOX_LANES:
        rows = [
            {name: value for name, value in row._mapping.items() if name != RUNNER_COLUMN}
            for row in source_conn.execute(source.select(table).order_by(table.c.seq))
        ]
        if rows:
            destination_conn.execute(destination.insert(table), rows)
    held = {
        row.lane: row.last_seq
        for row in destination_conn.execute(
            destination.select(outbox_counters, outbox_counters.c.lane, outbox_counters.c.last_seq)
        )
    }
    for row in source_conn.execute(
        source.select(outbox_counters, outbox_counters.c.lane, outbox_counters.c.last_seq).order_by(
            outbox_counters.c.lane
        )
    ):
        if row.lane not in held:
            destination_conn.execute(destination.insert(outbox_counters).values(lane=row.lane, last_seq=row.last_seq))
        elif row.last_seq > held[row.lane]:
            destination_conn.execute(
                destination.update(outbox_counters)
                .where(outbox_counters.c.lane == row.lane)
                .values(last_seq=row.last_seq)
            )


def enqueue_transcript_final(store: RunnerStoreConnections, conn: Connection, segment: Any, *, at: datetime) -> None:
    """Enqueue a marker noting ``segment`` is finalized — a minimal row; the
    wire-shaped ``TranscriptSegmentRecord`` itself is rendered at the drain boundary from
    the ledger row (``bzh:dependency-inversion``). Ships unconditionally. Shared by the
    leases adapter (a spawn/closure boundary implicitly finalizes) and the transcripts
    adapter (an explicit finalize)."""
    insert_outbox_row(
        store,
        conn,
        transcript_outbound_buffer,
        segment_id=str(segment.segment_id),
        chunk_id=str(segment.chunk_id),
        final=True,
        payload=json.dumps({"segment_id": str(segment.segment_id)}),
        created_at=at,
    )
