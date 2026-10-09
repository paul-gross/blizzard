"""SQLAlchemy adapter for the trace export seam (package-private).

Reads the cursor and its latch, and appends the cursor's fact rows (``bzh:facts-not-status``). The
closing-fact candidates and whole-chunk :class:`StepFacts` span every tenant, so they come from the
sweep corpus (:class:`IReadStepCorpus`); this store keeps its own tenant's chunks and adds the graphs
and runner names they name."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime

from sqlalchemy import insert, select

from blizzard.foundation.event_log import HubEventKind
from blizzard.hub.domain.chunk.model import WorkRefLabel
from blizzard.hub.domain.graph.model import Graph, IReadManyGraphs
from blizzard.hub.domain.observability.hub_events import IReadHubEventLog
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
from blizzard.hub.tenancy import TenantId

_FAILED: HubEventKind = "trace-export-failed"
_LATCH_KINDS: tuple[HubEventKind, ...] = (_FAILED, "trace-export-recovered")


class TraceStore:
    """The trace export's reads and its cursor's appends. The cursor is hub-level, and so are the export's failure and
    recovery events, read from ``hub_events``, the hub's own event log its sweep records them in. ``tenant`` names the
    tenant whose chunks this store hydrates."""

    def __init__(
        self,
        store: HubStoreConnections,
        *,
        hub_events: IReadHubEventLog,
        tenant: Callable[[], TenantId],
        corpus: IReadStepCorpus,
        graphs: IReadManyGraphs,
        names: IReadRunnerNames,
        label: WorkRefLabel,
    ) -> None:
        self._store = store
        self._hub_events = hub_events
        self._tenant = tenant
        self._corpus = corpus
        self._graphs = graphs
        self._names = names
        self._label = label

    # --- candidates and hydration ---------------------------------------------------

    def closing_candidates(self, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
        return self._corpus.closing_candidates(since, until, limit)

    def step_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, StepFacts]:
        """The corpus's step facts of this store's own tenant — the one its graph and registry reads are
        scoped to — each with the graphs and runner names its facts reference.
        Another tenant's chunk is left out, since its graphs, runner names and work-ref labels are not
        this store's to read: a window or page holding it exports none of its rows, and the cursor
        still passes its closing facts."""
        tenant = self._tenant()
        corpus = self._corpus.step_facts_for(chunk_ids, self._label)
        built = {chunk_id: facts for chunk_id, facts in corpus.items() if facts.tenant == tenant}
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
        event = self._hub_events.newest((_FAILED,))
        return TraceExportFailure(event.at, event.message) if event is not None else None

    def newest_export_latch(self) -> HubEventKind | None:
        """The newer of the two latch kinds' newest events — run once per process start, not per pass."""
        event = self._hub_events.newest(_LATCH_KINDS)
        return next((k for k in _LATCH_KINDS if event is not None and k == event.kind), None)

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
