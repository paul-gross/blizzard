"""SQLAlchemy adapter for the lease trace seam (package-private).

Hydrates :class:`~blizzard.runner.domain.tracing.facts.LeaseTraceFacts` for closed leases only,
selecting the columns the bundle declares and never a content column. The plural read issues a
fixed set of statements per :func:`~blizzard.foundation.store.batching.id_batches` slice — one
lease/context/closure read, then one read per fact table — however many leases the slice holds."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Connection, Row, and_, func, insert, or_, select

from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.roles import entity
from blizzard.foundation.store.batching import id_batches
from blizzard.runner.domain.invocation_boundaries import InvocationBoundaryKind
from blizzard.runner.domain.tracing.cursor import LeaseCursorKey
from blizzard.runner.domain.tracing.facts import (
    BoundaryFact,
    CheckResultFact,
    ChecksRanFact,
    ContextSampleFact,
    LeaseClosureFact,
    LeaseContextFact,
    LeaseGrantFact,
    LeaseTraceFacts,
    NudgeFact,
    OverloadFact,
    ParkFact,
    ParkResumeFact,
    PauseParkFact,
    PauseResumeFact,
    SessionEndFact,
    SpawnFact,
    TakeoverEndFact,
    TakeoverFact,
    TokenUsageFact,
)
from blizzard.runner.domain.tracing.repository import IWriteLeaseTraces, LeaseTraceCheckpoint, LeaseTraceExportFailure
from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.internal.base import decode_work_refs
from blizzard.runner.store.schema import (
    check_results,
    checks_ran,
    context_samples,
    invocation_boundaries,
    lease_closures,
    lease_context,
    lease_spawns,
    leases,
    nudge_facts,
    outbound_buffer,
    overload_facts,
    park_facts,
    park_resumes,
    pause_park_resumes,
    pause_parks,
    session_ends,
    takeover_ends,
    takeovers,
    trace_cursor,
    trace_export_latch,
    usage_facts,
)


@entity
@dataclass(frozen=True)
class _FactRead:
    """One ``LeaseTraceFacts`` row-tuple field: the lease it keys on, the columns its row declares, and its builder.

    ``via`` joins a table that names only a parent row (``takeover_ends`` → ``takeovers``) to its lease."""

    field: str
    lease_id: Any
    columns: tuple[Any, ...]
    build: Callable[[Row[Any]], Any]
    via: tuple[Any, Any] | None = None

    def statement(self, ids: Sequence[str]):  # type: ignore[no-untyped-def]
        stmt = select(self.lease_id.label("fact_lease_id"), *self.columns)
        if self.via is not None:
            stmt = stmt.join(*self.via)
        return stmt.where(self.lease_id.in_(ids)).order_by(self.columns[0])


_FACT_READS = (
    _FactRead(
        "spawns",
        lease_spawns.c.lease_id,
        (
            lease_spawns.c.id,
            lease_spawns.c.spawned_at,
            lease_spawns.c.harness_id,
            lease_spawns.c.harness_version,
            lease_spawns.c.session_id,
            lease_spawns.c.identified_at,
        ),
        lambda r: SpawnFact(
            id=r.id,
            spawned_at=r.spawned_at,
            harness_id=r.harness_id,
            harness_version=r.harness_version,
            session_id=r.session_id,
            identified_at=r.identified_at,
        ),
    ),
    _FactRead(
        "boundaries",
        invocation_boundaries.c.lease_id,
        (
            invocation_boundaries.c.id,
            invocation_boundaries.c.generation,
            invocation_boundaries.c.kind,
            invocation_boundaries.c.opened_at,
            invocation_boundaries.c.closed_at,
        ),
        lambda r: BoundaryFact(
            id=r.id,
            generation=r.generation,
            kind=cast(InvocationBoundaryKind, r.kind),
            opened_at=r.opened_at,
            closed_at=r.closed_at,
        ),
    ),
    _FactRead(
        "usage",
        usage_facts.c.lease_id,
        (
            usage_facts.c.id,
            usage_facts.c.generation,
            usage_facts.c.kind,
            usage_facts.c.model,
            usage_facts.c.input_tokens,
            usage_facts.c.output_tokens,
            usage_facts.c.cache_read_tokens,
            usage_facts.c.cache_create_tokens,
            usage_facts.c.recorded_at,
            usage_facts.c.cost_usd,
            usage_facts.c.estimated_cost_usd,
            usage_facts.c.harness_id,
            usage_facts.c.harness_version,
        ),
        lambda r: TokenUsageFact(
            id=r.id,
            generation=r.generation,
            kind=r.kind,
            model=r.model,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cache_read_tokens=r.cache_read_tokens,
            cache_create_tokens=r.cache_create_tokens,
            recorded_at=r.recorded_at,
            cost_usd=r.cost_usd,
            estimated_cost_usd=r.estimated_cost_usd,
            harness_id=r.harness_id,
            harness_version=r.harness_version,
        ),
    ),
    _FactRead(
        "session_ends",
        session_ends.c.lease_id,
        (session_ends.c.id, session_ends.c.ended_at),
        lambda r: SessionEndFact(id=r.id, ended_at=r.ended_at),
    ),
    _FactRead(
        "context_samples",
        context_samples.c.lease_id,
        (context_samples.c.id, context_samples.c.sampled_at, context_samples.c.context_tokens),
        lambda r: ContextSampleFact(id=r.id, sampled_at=r.sampled_at, context_tokens=r.context_tokens),
    ),
    _FactRead(
        "parks",
        park_facts.c.lease_id,
        (park_facts.c.id, park_facts.c.question_id, park_facts.c.parked_at),
        lambda r: ParkFact(id=r.id, question_id=r.question_id, parked_at=r.parked_at),
    ),
    _FactRead(
        "park_resumes",
        park_resumes.c.lease_id,
        (park_resumes.c.id, park_resumes.c.question_id, park_resumes.c.resumed_at),
        lambda r: ParkResumeFact(id=r.id, question_id=r.question_id, resumed_at=r.resumed_at),
    ),
    _FactRead(
        "pause_parks",
        pause_parks.c.lease_id,
        (pause_parks.c.id, pause_parks.c.parked_at),
        lambda r: PauseParkFact(id=r.id, parked_at=r.parked_at),
    ),
    _FactRead(
        "pause_resumes",
        pause_park_resumes.c.lease_id,
        (pause_park_resumes.c.id, pause_park_resumes.c.resumed_at),
        lambda r: PauseResumeFact(id=r.id, resumed_at=r.resumed_at),
    ),
    _FactRead(
        "overloads",
        overload_facts.c.lease_id,
        (
            overload_facts.c.id,
            overload_facts.c.generation,
            overload_facts.c.streak_ordinal,
            overload_facts.c.observed_at,
            overload_facts.c.resume_after,
        ),
        lambda r: OverloadFact(
            id=r.id,
            generation=r.generation,
            streak_ordinal=r.streak_ordinal,
            observed_at=r.observed_at,
            resume_after=r.resume_after,
        ),
    ),
    _FactRead(
        "takeovers",
        takeovers.c.lease_id,
        (takeovers.c.opened_at, takeovers.c.takeover_id),
        lambda r: TakeoverFact(takeover_id=r.takeover_id, opened_at=r.opened_at),
    ),
    _FactRead(
        "takeover_ends",
        takeovers.c.lease_id,
        (takeover_ends.c.id, takeover_ends.c.takeover_id, takeover_ends.c.ended_at),
        lambda r: TakeoverEndFact(id=r.id, takeover_id=r.takeover_id, ended_at=r.ended_at),
        via=(takeovers, takeovers.c.takeover_id == takeover_ends.c.takeover_id),
    ),
    _FactRead(
        "nudges",
        nudge_facts.c.lease_id,
        (nudge_facts.c.id, nudge_facts.c.epoch, nudge_facts.c.nudged_at),
        lambda r: NudgeFact(id=r.id, epoch=r.epoch, nudged_at=r.nudged_at),
    ),
    _FactRead(
        "check_results",
        check_results.c.lease_id,
        (check_results.c.id, check_results.c.epoch, check_results.c.passed),
        lambda r: CheckResultFact(id=r.id, epoch=r.epoch, passed=bool(r.passed)),
    ),
    _FactRead(
        "checks_ran",
        checks_ran.c.lease_id,
        (checks_ran.c.id, checks_ran.c.epoch, checks_ran.c.ran_at),
        lambda r: ChecksRanFact(id=r.id, epoch=r.epoch, ran_at=r.ran_at),
    ),
)

# A lease closes once; should a second closure ever land, the first is the one that closed it.
_FIRST_CLOSURE = (
    select(lease_closures.c.lease_id, func.min(lease_closures.c.id).label("closure_id"))
    .group_by(lease_closures.c.lease_id)
    .subquery()
)


def _work_ref_tokens(raw: str | None) -> tuple[str, ...]:
    """Each ref's hub-rendered source-native token; a ref no source rendered carries none, as on the hub."""
    return tuple(stamp.label for stamp in decode_work_refs(raw) or () if stamp.label is not None)


def _closed_leases(conn: Connection, ids: Sequence[str]) -> list[Row[Any]]:
    stmt = (
        select(
            leases.c.lease_id,
            leases.c.chunk_id,
            leases.c.epoch,
            leases.c.runner_id,
            leases.c.created_at,
            lease_context.c.graph_id,
            lease_context.c.node_id,
            lease_context.c.node_name,
            lease_context.c.graph_name,
            lease_context.c.work_refs,
            lease_context.c.session_name,
            lease_context.c.resolved_model,
            lease_context.c.resolved_effort,
            lease_closures.c.reason,
            lease_closures.c.closed_at,
        )
        .join(lease_context, lease_context.c.lease_id == leases.c.lease_id)
        .join(_FIRST_CLOSURE, _FIRST_CLOSURE.c.lease_id == leases.c.lease_id)
        .join(lease_closures, lease_closures.c.id == _FIRST_CLOSURE.c.closure_id)
        .where(leases.c.lease_id.in_(ids))
    )
    return list(conn.execute(stmt))


def _facts(conn: Connection, ids: Sequence[str]) -> dict[str, LeaseTraceFacts]:
    closed = _closed_leases(conn, ids)
    if not closed:
        return {}
    closed_ids = [str(r.lease_id) for r in closed]
    rows: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for read in _FACT_READS:
        for r in conn.execute(read.statement(closed_ids)):
            rows[str(r.fact_lease_id)][read.field].append(read.build(r))
    return {
        str(r.lease_id): LeaseTraceFacts(
            lease=LeaseGrantFact(
                lease_id=str(r.lease_id),
                chunk_id=str(r.chunk_id),
                epoch=int(r.epoch),
                runner_id=str(r.runner_id),
                created_at=r.created_at,
            ),
            context=LeaseContextFact(
                graph_id=str(r.graph_id),
                node_id=str(r.node_id),
                node_name=str(r.node_name),
                graph_name=r.graph_name,
                work_refs=_work_ref_tokens(r.work_refs),
                session_name=r.session_name,
                resolved_model=r.resolved_model,
                resolved_effort=r.resolved_effort,
            ),
            closure=LeaseClosureFact(reason=str(r.reason), closed_at=r.closed_at),
            **{field: tuple(found) for field, found in rows[str(r.lease_id)].items()},
        )
        for r in closed
    }


_LATCH_KINDS: tuple[EventLogKind, ...] = ("trace-export-failed", "trace-export-recovered")


class LeaseTraceFactsStore:
    """Lease trace adapter over the runner store engine: closed-lease facts, the sweep's window, cursor and latch."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def lease_trace_facts(self, lease_id: str) -> LeaseTraceFacts | None:
        with self._store.connect() as conn:
            return _facts(conn, [lease_id]).get(lease_id)

    def lease_trace_facts_for(self, lease_ids: Collection[str]) -> dict[str, LeaseTraceFacts]:
        result: dict[str, LeaseTraceFacts] = {}
        with self._store.connect() as conn:
            for batch in id_batches(sorted(set(lease_ids))):
                result.update(_facts(conn, batch))
        return result

    def closed_leases_after(self, since: LeaseCursorKey, until: datetime, limit: int) -> tuple[LeaseCursorKey, ...]:
        c = lease_closures.c
        earlier = lease_closures.alias("earlier")
        stmt = (
            select(c.closed_at, c.lease_id)
            .where(
                c.closed_at >= since.at,
                c.closed_at <= until,
                or_(c.closed_at > since.at, c.lease_id > since.lease_id),
                ~select(earlier.c.id).where(and_(earlier.c.lease_id == c.lease_id, earlier.c.id < c.id)).exists(),
            )
            .order_by(c.closed_at, c.lease_id)
            .limit(limit)
        )
        with self._store.connect() as conn:
            return tuple(LeaseCursorKey(r.closed_at, str(r.lease_id)) for r in conn.execute(stmt))

    def oldest_unsent_lease(self, since: LeaseCursorKey, until: datetime) -> LeaseCursorKey | None:
        return next(iter(self.closed_leases_after(since, until, 1)), None)

    def newest_trace_cursor(self) -> LeaseTraceCheckpoint | None:
        return self._newest_cursor()

    def newest_export_cursor(self) -> LeaseTraceCheckpoint | None:
        """The newest row that told spans — rows of zero are jumps and idle advances."""
        return self._newest_cursor(trace_cursor.c.span_count > 0)

    def _newest_cursor(self, *where) -> LeaseTraceCheckpoint | None:  # type: ignore[no-untyped-def]
        c = trace_cursor.c
        with self._store.connect() as conn:
            row = conn.execute(
                select(trace_cursor).where(*where).order_by(c.recorded_at.desc(), c.id.desc()).limit(1)
            ).first()
        if row is None:
            return None
        return LeaseTraceCheckpoint(LeaseCursorKey(row.position_at, row.lease_id), row.span_count, row.recorded_at)

    def newest_trace_latch(self) -> EventLogKind | None:
        c = trace_export_latch.c
        with self._store.connect() as conn:
            kind = conn.execute(
                select(c.kind).order_by(c.recorded_at.desc(), c.id.desc()).limit(1)
            ).scalar_one_or_none()
        return next((k for k in _LATCH_KINDS if k == kind), None)

    def newest_export_failure(self) -> LeaseTraceExportFailure | None:
        c = trace_export_latch.c
        stmt = select(c.recorded_at).where(c.kind == "trace-export-failed").order_by(c.recorded_at.desc(), c.id.desc())
        with self._store.connect() as conn:
            at = conn.execute(stmt.limit(1)).scalar_one_or_none()
        return LeaseTraceExportFailure(at) if at is not None else None

    def append_trace_cursor(self, record: LeaseTraceCheckpoint) -> None:
        with self._store.begin() as conn:
            conn.execute(
                insert(trace_cursor).values(
                    position_at=record.position.at,
                    lease_id=record.position.lease_id,
                    span_count=record.span_count,
                    recorded_at=record.recorded_at,
                )
            )

    def record_trace_latch(self, kind: EventLogKind, *, at: datetime, report_kind: str, report_payload: str) -> int:
        # One transaction: a kill between the two would either re-announce after restart or never announce.
        with self._store.begin() as conn:
            conn.execute(insert(trace_export_latch).values(kind=kind, recorded_at=at))
            result = conn.execute(
                insert(outbound_buffer).values(
                    kind=report_kind, chunk_id=None, lease_id=None, payload=report_payload, created_at=at
                )
            )
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0


def _conforms_lease_trace_facts_store(x: LeaseTraceFactsStore) -> IWriteLeaseTraces:
    return x
