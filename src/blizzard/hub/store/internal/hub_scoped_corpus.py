"""The sweep corpus member group of :class:`HubScopedReads` — the reads a hub sweep's one pass
takes its corpus from, across every tenant: forge-status annotation, the close drain, event
derivation, and the trace and egress exports — and the flow driver's change probe.

Each corpus read answers, beside every row, the tenant the row belongs to, and leaves out every
closed tenant — one with a ``tenant_deletions`` row, whether its drain is still running or done —
through the one predicate ``corpus_rows.open_tenant``, applied to every tenant-owned table a read
returns rows of; the same predicate leaves out a row naming no tenant. A probe that gates a pass
(``bzh:probe-gated-pass``) is read hub-wide in one statement and answers per tenant
(``bzh:pass-isolates-tenants``): it counts every tenant's rows, closed or not. Chunk ids are
hub-minted, so a filter on a chunk id needs no tenant beside it; a segment id is runner-minted, so a
read that matches one segment row against another matches the tenant too. The group reads and never
writes: whatever a pass then does with a row, it does in that row's tenant. The trace and egress
reads' statements live in ``corpus_rows`` and run on the marked connection this group opens."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

from sqlalchemy import CompoundSelect, Connection, Select, Table, and_, func, literal, null, select, union_all

from blizzard.hub.domain.chunk.model import PendingCloseIntent, WorkRef, WorkRefLabel
from blizzard.hub.domain.chunk.ports.work_refs import IReadWorkRefsCorpus, TenantWorkRef, WorkRefsSignature
from blizzard.hub.domain.delivery.ports.flows import IReadFlowLinesCorpus, LineSignature
from blizzard.hub.domain.observability.analytics.derivation import IReadDerivationCorpus
from blizzard.hub.domain.observability.analytics.events import (
    CandidacyRead,
    DerivationMarker,
    DerivationSignature,
    DropFact,
    is_candidate,
)
from blizzard.hub.domain.observability.egress.event_rows import EventDerivation
from blizzard.hub.domain.observability.egress.repository import (
    EpochKey,
    EventsPosition,
    IReadEgressEvents,
    IReadUsageCorpus,
    UsagePosition,
)
from blizzard.hub.domain.observability.egress.rows import AttributedUsage
from blizzard.hub.domain.observability.tracing.facts import StepFacts
from blizzard.hub.domain.observability.tracing.repository import ClosingCandidates, IReadStepCorpus
from blizzard.hub.domain.work_items.closure import IReadCloseIntentCorpus, TenantCloseIntent
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import corpus_rows
from blizzard.hub.store.internal.chunk_facts_store import status_family_tables
from blizzard.hub.store.internal.corpus_rows import open_tenant
from blizzard.hub.store.internal.transcript_event_store import fingerprint_from_parts
from blizzard.hub.tenancy import TenantId

#: Opens a read, named by its operation, whose every statement is marked hub-scoped — the aggregate's
#: ``hub_scoped_read`` bound to the hub-level connections, so this group never holds an unmarked one.
MarkedRead = Callable[[str], AbstractContextManager[Connection]]

_EPHEMERAL_TABLES = (s.chunk_grouped, s.chunk_deleted)


def _ephemeral_chunk_ids() -> CompoundSelect[Any]:
    """Every tenant's grouped-away and deleted chunk ids — gone from every corpus read."""
    first, *rest = (select(t.c.chunk_id) for t in _EPHEMERAL_TABLES)
    return first.union(*rest)


def _visible_segments() -> Select[Any]:
    """Every open tenant's visible segments, ``(tenant_id, segment_id)``: final, of a minted chunk,
    and superseded by no segment of the same tenant."""
    segment = s.transcript_segments
    successor = segment.alias("successor")
    superseded = (
        select(successor.c.id)
        .where(successor.c.supersedes == segment.c.segment_id, successor.c.tenant_id == segment.c.tenant_id)
        .exists()
    )
    return (
        select(segment.c.tenant_id, segment.c.segment_id)
        .where(
            open_tenant(segment),
            segment.c.final.is_(True),
            ~superseded,
            segment.c.chunk_id.in_(select(s.chunks.c.chunk_id)),
        )
        .distinct()
    )


class SweepCorpusReads:
    """The forge-status, close-drain, event-derivation, trace and egress sweeps' corpus reads and probes, and the flow
    driver's probe."""

    def __init__(self, read: MarkedRead) -> None:
        self._read = read

    # --- forge-status annotation ------------------------------------------------------

    def live_work_refs(self) -> list[TenantWorkRef]:
        """Every open tenant's work-ref rows whose chunk is neither grouped away nor deleted,
        each with its tenant and chunk; which of them are live is the caller's derivation."""
        refs = s.chunk_work_refs
        with self._read("corpus live work refs") as conn:
            rows = conn.execute(
                select(refs.c.tenant_id, refs.c.chunk_id, refs.c.source, refs.c.ref)
                .where(open_tenant(refs), refs.c.chunk_id.not_in(_ephemeral_chunk_ids()))
                .order_by(refs.c.id)
            ).all()
        return [
            TenantWorkRef(
                tenant=TenantId(row.tenant_id), chunk_id=row.chunk_id, ref=WorkRef(source=row.source, ref=row.ref)
            )
            for row in rows
        ]

    def live_work_refs_signature(self) -> dict[TenantId, WorkRefsSignature]:
        """One grouped statement of append-only aggregates per tenant; closed tenants included."""
        tables = sorted((*status_family_tables(), s.chunk_work_refs, *_EPHEMERAL_TABLES), key=lambda t: t.name)
        marks = self._signature_marks(tables, "corpus live work refs signature")
        return {tenant: WorkRefsSignature(marks=by_table) for tenant, by_table in marks.items()}

    # --- flow driver ------------------------------------------------------------------

    def flow_lines_signature(self) -> dict[TenantId, LineSignature]:
        """The status families' aggregates, per tenant, in one grouped statement; closed tenants included."""
        tables = sorted(status_family_tables(), key=lambda t: t.name)
        marks = self._signature_marks(tables, "corpus flow lines signature")
        return {tenant: LineSignature(marks=by_table) for tenant, by_table in marks.items()}

    def _signature_marks(
        self, tables: Sequence[Table], operation: str
    ) -> dict[TenantId, tuple[tuple[str, int, int | None], ...]]:
        """Each tenant's ``(table, count, max(id))`` per table of ``tables``, read hub-wide in one statement."""
        per_table = [
            select(
                table.c.tenant_id.label("tenant_id"),
                literal(table.name).label("table_name"),
                func.count().label("row_count"),
                (func.max(table.c.id) if "id" in table.c else null()).label("max_id"),
            ).group_by(table.c.tenant_id)
            for table in tables
        ]
        with self._read(operation) as conn:
            rows = conn.execute(union_all(*per_table)).all()
        seen: dict[str, dict[str, tuple[int, int | None]]] = {}
        for row in rows:
            seen.setdefault(row.tenant_id, {})[row.table_name] = (row.row_count, row.max_id)
        return {
            TenantId(tenant): tuple((table.name, *by_table.get(table.name, (0, None))) for table in tables)
            for tenant, by_table in seen.items()
        }

    # --- close drain ------------------------------------------------------------------

    def pending_close_intents(self) -> list[TenantCloseIntent]:
        """Every open tenant's unretired close intent whose chunk is neither grouped away nor
        deleted, with its attempt history, in intent order."""
        attempt, intent = s.close_intent_attempts, s.close_intents
        of_intent = and_(attempt.c.tenant_id == intent.c.tenant_id, attempt.c.intent_id == intent.c.id)
        attempt_count = select(func.count(attempt.c.id)).where(of_intent).scalar_subquery()
        last_attempt_at = select(func.max(attempt.c.attempted_at)).where(of_intent).scalar_subquery()
        with self._read("corpus pending close intents") as conn:
            rows = conn.execute(
                select(
                    intent.c.tenant_id,
                    intent.c.id,
                    intent.c.chunk_id,
                    intent.c.source,
                    intent.c.ref,
                    attempt_count.label("attempt_count"),
                    last_attempt_at.label("last_attempt_at"),
                )
                .where(open_tenant(intent), intent.c.retired_at.is_(None))
                .where(intent.c.chunk_id.not_in(_ephemeral_chunk_ids()))
                .order_by(intent.c.id)
            ).all()
        return [
            TenantCloseIntent(
                tenant=TenantId(row.tenant_id),
                intent=PendingCloseIntent(
                    chunk_id=row.chunk_id,
                    ref=WorkRef(source=row.source, ref=row.ref),
                    intent_id=row.id,
                    attempt_count=row.attempt_count or 0,
                    last_attempt_at=row.last_attempt_at,
                ),
            )
            for row in rows
        ]

    # --- event derivation -------------------------------------------------------------

    def derivation_signature(self) -> dict[TenantId, DerivationSignature]:
        """One grouped statement of segment and chunk aggregates per tenant."""
        segment, chunk = s.transcript_segments, s.chunks
        segments = select(
            segment.c.tenant_id.label("tenant_id"),
            func.count(segment.c.id).label("segment_count"),
            func.max(segment.c.id).label("max_segment_id"),
            func.max(segment.c.received_at).label("max_received_at"),
            literal(0).label("chunk_count"),
        ).group_by(segment.c.tenant_id)
        chunks = select(
            chunk.c.tenant_id.label("tenant_id"),
            literal(0).label("segment_count"),
            null().label("max_segment_id"),
            null().label("max_received_at"),
            func.count().label("chunk_count"),
        ).group_by(chunk.c.tenant_id)
        with self._read("corpus derivation signature") as conn:
            rows = conn.execute(union_all(segments, chunks)).all()
        merged: dict[str, dict[str, Any]] = {}
        for row in rows:
            into = merged.setdefault(
                row.tenant_id, {"segment_count": 0, "max_segment_id": None, "max_received_at": None, "chunk_count": 0}
            )
            into["segment_count"] += row.segment_count
            into["chunk_count"] += row.chunk_count
            if row.max_segment_id is not None:
                into["max_segment_id"] = row.max_segment_id
                into["max_received_at"] = row.max_received_at
        return {TenantId(tenant): DerivationSignature(**values) for tenant, values in merged.items()}

    def candidacy(self, extractor_version: str) -> dict[TenantId, CandidacyRead]:
        """Each open tenant's visible segments, and which of them disagree with their current
        ``extractor_version`` marker or carry none — keyed by tenant; a tenant with no visible
        segment has no entry."""
        segment = s.transcript_segments
        visible = _visible_segments().subquery()
        marker = s.transcript_event_derivations
        with self._read("corpus candidacy") as conn:
            digest_rows = conn.execute(
                select(
                    segment.c.tenant_id,
                    segment.c.segment_id,
                    segment.c.content_digest,
                    segment.c.harness_version,
                )
                .join(
                    visible,
                    and_(visible.c.tenant_id == segment.c.tenant_id, visible.c.segment_id == segment.c.segment_id),
                )
                .order_by(segment.c.tenant_id, segment.c.segment_id, segment.c.turn_range_start)
            ).all()
            markers = {
                (row.tenant_id, row.segment_id): row.content_fingerprint
                for row in conn.execute(
                    select(marker.c.tenant_id, marker.c.segment_id, marker.c.content_fingerprint).where(
                        open_tenant(marker), marker.c.extractor_version == extractor_version
                    )
                )
            }
        visible_ids: dict[TenantId, set[str]] = {}
        candidates: dict[TenantId, list[str]] = {}
        for (tenant_id, segment_id), rows in itertools.groupby(digest_rows, key=lambda r: (r.tenant_id, r.segment_id)):
            tenant = TenantId(tenant_id)
            visible_ids.setdefault(tenant, set()).add(segment_id)
            fingerprint = fingerprint_from_parts([(row.content_digest, row.harness_version) for row in rows])
            if is_candidate(markers.get((tenant_id, segment_id)), fingerprint):
                candidates.setdefault(tenant, []).append(segment_id)
        return {
            tenant: CandidacyRead(visible_segment_ids=frozenset(ids), candidate_segment_ids=candidates.get(tenant, []))
            for tenant, ids in visible_ids.items()
        }

    def derived_segment_ids(self) -> dict[TenantId, frozenset[str]]:
        """Each open tenant's segments carrying any derivation marker, keyed by tenant."""
        marker = s.transcript_event_derivations
        with self._read("corpus derived segment ids") as conn:
            rows = conn.execute(
                select(marker.c.tenant_id, marker.c.segment_id)
                .where(open_tenant(marker))
                .distinct()
                .order_by(marker.c.tenant_id, marker.c.segment_id)
            ).all()
        return {
            TenantId(tenant_id): frozenset(row.segment_id for row in group)
            for tenant_id, group in itertools.groupby(rows, key=lambda r: r.tenant_id)
        }

    # --- trace and egress exports -----------------------------------------------------

    def closing_candidates(self, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
        with self._read("corpus closing candidates") as conn:
            return corpus_rows.closing_candidates(conn, since, until, limit)

    def step_facts_for(
        self, chunk_ids: Sequence[str], label: Callable[[TenantId], WorkRefLabel]
    ) -> dict[str, StepFacts]:
        """Each chunk's facts, its work refs rendered by its own tenant's ``label`` once the read is done."""
        if not chunk_ids:
            return {}
        with self._read("corpus step facts") as conn:
            read = corpus_rows.step_facts_for(conn, chunk_ids)
        return {
            chunk_id: corpus_rows.with_work_refs(facts, refs, label(tenant))
            for chunk_id, (tenant, facts, refs) in read.items()
        }

    def usage_after(self, position: UsagePosition, until: datetime, limit: int) -> list[AttributedUsage]:
        with self._read("corpus usage after") as conn:
            return corpus_rows.usage_after(conn, position, until, limit)

    def markers_after(
        self, position: EventsPosition, until: datetime, limit: int, *, extractor_version: str | None
    ) -> list[DerivationMarker]:
        with self._read("corpus markers after") as conn:
            return corpus_rows.markers_after(conn, position, until, limit, extractor_version)

    def drops_after(self, position: EventsPosition, until: datetime, limit: int) -> list[DropFact]:
        with self._read("corpus drops after") as conn:
            return corpus_rows.drops_after(conn, position, until, limit)

    def derivations(self, markers: Sequence[DerivationMarker]) -> list[EventDerivation]:
        with self._read("corpus derivations") as conn:
            return corpus_rows.derivations(conn, markers)

    def epochs_minted_between(
        self, since: datetime, until: datetime, after: EpochKey | None, limit: int
    ) -> list[EpochKey]:
        with self._read("corpus epochs minted between") as conn:
            return corpus_rows.epochs_minted_between(conn, since, until, after, limit)

    def epoch_markers(self, epochs: Sequence[EpochKey], *, extractor_version: str | None) -> list[DerivationMarker]:
        with self._read("corpus epoch markers") as conn:
            return corpus_rows.epoch_markers(conn, epochs, extractor_version)

    def epoch_drops(self, epochs: Sequence[EpochKey]) -> list[DropFact]:
        with self._read("corpus epoch drops") as conn:
            return corpus_rows.epoch_drops(conn, epochs)


_read_work_refs_corpus_conforms: type[IReadWorkRefsCorpus] = SweepCorpusReads
_read_close_intent_corpus_conforms: type[IReadCloseIntentCorpus] = SweepCorpusReads
_read_derivation_corpus_conforms: type[IReadDerivationCorpus] = SweepCorpusReads
_read_step_corpus_conforms: type[IReadStepCorpus] = SweepCorpusReads
_read_usage_corpus_conforms: type[IReadUsageCorpus] = SweepCorpusReads
_read_egress_events_conforms: type[IReadEgressEvents] = SweepCorpusReads
_read_flow_lines_corpus_conforms: type[IReadFlowLinesCorpus] = SweepCorpusReads
