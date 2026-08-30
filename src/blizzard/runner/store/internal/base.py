"""Package-private infrastructure shared by every ``runner/store/internal/`` adapter
(blizzard#410, D1): the connection helper each concept adapter takes in place of a bare
``Engine``, and the fact-closure predicates more than one concept's rows share — a
predicate used by exactly one concept is defined at that concept's own adapter instead."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, Row, Select, select
from sqlalchemy.exc import SQLAlchemyError

from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.schema import (
    binding_releases,
    env_bindings,
    escalation_closures,
    lease_closures,
    lease_context,
    leases,
    pause_park_resumes,
    pause_parks,
    resume_clears,
    resume_intents,
    transcript_outbound_buffer,
)

#: A fresh segment's placeholder, before its first pump read — restated rather than
#: imported (the store never depends on the harness seam). Shared by the leases and transcripts adapters.
NO_NORMALIZER_VERSION = ""


class RunnerStoreConnections:
    """The connection-acquiring collaborator every ``runner/store/internal/`` adapter
    takes in place of ``Engine`` (``bzh:dependency-injection``)."""

    def __init__(self, engine: Engine, errors: RunnerStoreErrorFactory) -> None:
        self._engine = engine
        self._errors = errors

    def connect(self) -> Connection:
        try:
            return self._engine.connect()
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="connect") from exc

    def begin(self) -> AbstractContextManager[Connection]:
        try:
            return self._engine.begin()
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="begin") from exc

    def all(self, stmt: Select[Any]) -> list[Row[Any]]:
        try:
            with self._engine.connect() as conn:
                return list(conn.execute(stmt))
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="query") from exc


@dataclass(frozen=True)
class Unsuperseded:
    """A fact row stands while no superseding row exists — one correlated ``NOT EXISTS``.

    Correlated on the superseding row's own ordering column, an instant or an epoch, never
    a bare key ``NOT IN``: a re-mark above an earlier close reads as open again."""

    marker: Any
    conditions: tuple[Any, ...]

    @property
    def clause(self):  # type: ignore[no-untyped-def]
        return ~select(self.marker).where(*self.conditions).exists()


@dataclass(frozen=True)
class Unclosed:
    """A row stands while no closing row names its id — a plain ``NOT IN``.

    Its key is a fresh ULID per open, so there is no re-open-under-the-same-key hazard for
    the correlated form above to guard against."""

    key: Any
    closers: Any

    @property
    def clause(self):  # type: ignore[no-untyped-def]
        return self.key.not_in(select(self.closers))


# Pinned by tests/test_pin_runner_store.py::test_a_rebind_after_a_release_reads_as_held.
HELD_BINDING = Unsuperseded(
    binding_releases.c.id,
    (
        binding_releases.c.chunk_id == env_bindings.c.chunk_id,
        binding_releases.c.environment_id == env_bindings.c.environment_id,
        binding_releases.c.released_at >= env_bindings.c.bound_at,
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

# A second pause under one lease is not masked by the first pause's resume.
OPEN_PAUSE_PARK = Unsuperseded(
    pause_park_resumes.c.id,
    (
        pause_park_resumes.c.lease_id == pause_parks.c.lease_id,
        pause_park_resumes.c.resumed_at >= pause_parks.c.parked_at,
    ),
)

#: The pause-park half of ask/park's ``parked_lease_ids`` union — shared so the ask
#: adapter never reaches into a sibling adapter for it (blizzard#410 review F3).
PAUSE_PARKED_LEASE_IDS = select(pause_parks.c.lease_id).where(OPEN_PAUSE_PARK.clause).distinct()

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


def lease_select():  # type: ignore[no-untyped-def]
    """The lease+context join every lease read selects from — shared with the transcripts
    ledger's backfill read, which also joins a lease (blizzard#410)."""
    return select(
        leases.c.lease_id,
        leases.c.chunk_id,
        leases.c.epoch,
        leases.c.runner_id,
        leases.c.pid,
        leases.c.process_start_time,
        leases.c.session_id,
        leases.c.created_at,
        lease_context.c.graph_id,
        lease_context.c.node_id,
        lease_context.c.node_name,
        lease_context.c.retries_max,
        # The session stamps (issue #144) — selected on the shared join rather than a
        # second query, so every lease read carries them.
        lease_context.c.session_name,
        lease_context.c.resolved_model,
        lease_context.c.resolved_effort,
        lease_context.c.resolved_compaction_window,
    ).join(lease_context, lease_context.c.lease_id == leases.c.lease_id)


def row_to_lease(r) -> LeaseRecord:  # type: ignore[no-untyped-def]
    return LeaseRecord(
        lease_id=str(r.lease_id),
        chunk_id=str(r.chunk_id),
        graph_id=str(r.graph_id),
        node_id=str(r.node_id),
        node_name=str(r.node_name),
        epoch=int(r.epoch),
        runner_id=str(r.runner_id),
        retries_max=int(r.retries_max),
        created_at=r.created_at,
        session_name=r.session_name,
        resolved_model=r.resolved_model,
        resolved_effort=r.resolved_effort,
        resolved_compaction_window=r.resolved_compaction_window,
        pid=int(r.pid) if r.pid is not None else None,
        process_start_time=str(r.process_start_time) if r.process_start_time is not None else None,
        session_id=str(r.session_id) if r.session_id is not None else None,
    )


def enqueue_transcript_final(conn: Connection, segment: Any, *, at: datetime) -> None:
    """Enqueue a marker noting ``segment`` is finalized (issue #246) — a minimal row; the
    wire-shaped ``TranscriptSegmentRecord`` itself is rendered at the drain boundary from
    the ledger row (``bzh:dependency-inversion``). Ships unconditionally. Shared by the
    leases adapter (a spawn/closure boundary implicitly finalizes) and the transcripts
    adapter (an explicit finalize)."""
    conn.execute(
        transcript_outbound_buffer.insert().values(
            segment_id=str(segment.segment_id),
            chunk_id=str(segment.chunk_id),
            final=True,
            payload=json.dumps({"segment_id": str(segment.segment_id)}),
            created_at=at,
        )
    )
