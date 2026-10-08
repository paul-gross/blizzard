"""SQLAlchemy adapter for the trace export seam (package-private).

Reads the cursor and its latch, and appends the cursor's fact rows (``bzh:facts-not-status``). The
closing-fact candidates and whole-chunk :class:`StepFacts` span every tenant, so they come from the
sweep corpus (:class:`IReadStepCorpus`); this store adds the graphs and runner names they name."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from sqlalchemy import insert, select

from blizzard.foundation.event_log import EventLogKind
from blizzard.hub.domain.chunk.model import WorkRefLabel
from blizzard.hub.domain.graph.model import Graph, IReadManyGraphs
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.facts import StepFacts
from blizzard.hub.domain.observability.tracing.repository import (
    ClosingCandidates,
    IReadRunnerNames,
    IReadStepCorpus,
    IReadTraceStatus,
    IWriteTraceCursor,
    TraceCheckpoint,
    TraceExportFailure,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.scoped import ScopedConnections

_FAILED: EventLogKind = "trace-export-failed"
_LATCH_KINDS: tuple[EventLogKind, ...] = (_FAILED, "trace-export-recovered")


class TraceStore:
    """The trace export's reads and its cursor's appends. The cursor is hub-level; the export's failure and recovery
    events are read from ``events``, the event log its sweep records them in."""

    def __init__(
        self,
        store: HubStoreConnections,
        *,
        events: ScopedConnections,
        corpus: IReadStepCorpus,
        graphs: IReadManyGraphs,
        names: IReadRunnerNames,
        label: WorkRefLabel,
    ) -> None:
        self._store = store
        self._events = events
        self._corpus = corpus
        self._graphs = graphs
        self._names = names
        self._label = label

    # --- candidates and hydration ---------------------------------------------------

    def closing_candidates(self, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
        return self._corpus.closing_candidates(since, until, limit)

    def step_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, StepFacts]:
        """The corpus's step facts, each with the graphs and runner names its facts reference — read
        through this store's own graph and registry reads."""
        built = self._corpus.step_facts_for(chunk_ids, self._label)
        graphs = self._graphs.get_many(sorted({g for facts in built.values() for g in _graph_ids(facts)}))
        names = self._names.names_for(sorted({r for facts in built.values() for r in _runner_ids(facts)}))
        return {chunk_id: _with_reads(facts, graphs, names) for chunk_id, facts in built.items()}

    # --- cursor and latch -----------------------------------------------------------

    def newest_cursor(self) -> TraceCheckpoint | None:
        return self._newest_cursor("newest_cursor")

    def newest_export_cursor(self) -> TraceCheckpoint | None:
        """The newest row that told spans — rows of zero are jumps and idle advances."""
        return self._newest_cursor("newest_export_cursor", s.trace_cursor.c.span_count > 0)

    def _newest_cursor(self, operation: str, *where) -> TraceCheckpoint | None:  # type: ignore[no-untyped-def]
        c = s.trace_cursor.c
        with self._store.read(operation) as conn:
            row = conn.execute(
                select(s.trace_cursor).where(*where).order_by(c.recorded_at.desc(), c.id.desc()).limit(1)
            ).first()
        if row is None:
            return None
        return TraceCheckpoint(
            CursorKey(row.position_at, row.chunk_id, row.epoch, row.decision_id), row.span_count, row.recorded_at
        )

    def newest_export_failure(self) -> TraceExportFailure | None:
        """The newest failure event — filtered in the query, so a later recovery never hides it."""
        c = s.event_log.c
        stmt = (
            self._events.select(s.event_log, c.kind, c.recorded_at, c.message)
            .where(c.kind == _FAILED)
            .order_by(c.recorded_at.desc(), c.id.desc())
            .limit(1)
        )
        with self._events.read("newest_export_failure") as conn:
            row = conn.execute(stmt).first()
        return TraceExportFailure(row.recorded_at, row.message) if row is not None else None

    def newest_export_latch(self) -> EventLogKind | None:
        """Walks the event log newest-first to the first match — run once per process start, not per pass."""
        c = s.event_log.c
        stmt = (
            self._events.select(s.event_log, c.kind)
            .where(c.kind.in_(_LATCH_KINDS))
            .order_by(c.recorded_at.desc(), c.id.desc())
            .limit(1)
        )
        with self._events.read("newest_export_latch") as conn:
            kind = conn.execute(stmt).scalar_one_or_none()
        return next((k for k in _LATCH_KINDS if k == kind), None)

    def append_cursor(self, record: TraceCheckpoint) -> None:
        position = record.position
        with self._store.write("append_cursor") as conn:
            conn.execute(
                insert(s.trace_cursor).values(
                    position_at=position.at,
                    chunk_id=position.chunk_id,
                    epoch=position.epoch,
                    decision_id=position.decision_id,
                    span_count=record.span_count,
                    recorded_at=record.recorded_at,
                )
            )


def _graph_ids(facts: StepFacts) -> set[str]:
    """Every graph a chunk's facts name — its pin and each movement fact's graphs."""
    ids = {facts.pin_graph_id} if facts.pin_graph_id is not None else set()
    ids |= {t.graph_id for t in facts.transitions}
    ids |= {m.from_graph_id for m in facts.migrations} | {m.to_graph_id for m in facts.migrations}
    ids |= {r.graph_id for r in facts.restarts}
    ids |= {r.from_graph_id for r in facts.restarts if r.from_graph_id is not None}
    return ids


def _runner_ids(facts: StepFacts) -> set[str]:
    """Every runner a chunk's facts name — each epoch's owner and each gate's imposing runner."""
    ids = {o.runner_id for o in facts.epoch_owners if o.runner_id is not None}
    return ids | {d.imposed_by_runner_id for d in facts.decisions if d.imposed_by_runner_id is not None}


def _with_reads(facts: StepFacts, graphs: dict[str, Graph], names: dict[str, str]) -> StepFacts:
    return replace(
        facts,
        graphs={g: graphs[g] for g in _graph_ids(facts) if g in graphs},
        runner_names={r: names[r] for r in _runner_ids(facts) if r in names},
    )


def _conforms_trace_store(x: TraceStore) -> tuple[IWriteTraceCursor, IReadTraceStatus]:
    return x, x
