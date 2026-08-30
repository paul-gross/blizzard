"""Package-private infrastructure shared by every ``runner/store/internal/`` adapter
(blizzard#410, D1): the connection helper each concept adapter takes in place of a bare
``Engine``, and the fact-closure predicates more than one concept's rows share — a
predicate used by exactly one concept is defined at that concept's own adapter instead."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import SQLAlchemyError

from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.schema import (
    binding_releases,
    env_bindings,
    escalation_closures,
    lease_closures,
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

    def connect(self):  # type: ignore[no-untyped-def]
        try:
            return self._engine.connect()
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="connect") from exc

    def begin(self):  # type: ignore[no-untyped-def]
        try:
            return self._engine.begin()
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="begin") from exc

    def all(self, stmt):  # type: ignore[no-untyped-def]
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
