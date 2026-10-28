"""SQLAlchemy adapter for the garden-run read seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). The window
itself is bound in SQL and the list is ordered by an explicit ``ORDER BY`` on
``chunks.minted_at`` (never incidental row order, ``bzh:sql-portable``); outcome
derivation is left entirely to `src/blizzard/hub/domain/garden/runs/history.py`, which reads the
chunk's own facts through its own seams."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Join, Row, func
from sqlalchemy.engine import Connection

from blizzard.foundation.run_mode import RunMode
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.garden.runs.history import (
    DeliveredSet,
    DeliveredSetRaw,
    IReadGardenRunRepository,
    RunDeliveries,
    RunIdentity,
)
from blizzard.hub.store.schema import (
    artifacts,
    chunk_work_refs,
    chunks,
    finding_facts,
    finding_sets,
    scopes,
    work_item_runs,
    work_items,
)
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements, statements_of


def _run_identity_join(stmts: ScopedStatements) -> Join:
    """The chunk -> chunk_work_refs -> work_items -> work_item_runs -> scopes join every read
    here starts from — a run's own identity, minted once at `RunService.run`'s own act. Each
    joined side is confined to the tenant in its own ``ON``, and the natural-key join onto
    ``work_items`` carries tenant equality there too."""
    with_refs = stmts.scope_join(chunks, chunk_work_refs, chunk_work_refs.c.chunk_id == chunks.c.chunk_id)
    with_items = stmts.scope_join(
        with_refs,
        work_items,
        (work_items.c.tenant_id == chunk_work_refs.c.tenant_id)
        & (work_items.c.source == chunk_work_refs.c.source)
        & (work_items.c.ref == chunk_work_refs.c.ref),
    )
    with_runs = stmts.scope_join(with_items, work_item_runs, work_item_runs.c.work_item_id == work_items.c.work_item_id)
    return stmts.scope_join(with_runs, scopes, scopes.c.scope_id == work_item_runs.c.scope_id)


_IDENTITY_COLUMNS = (
    chunks.c.chunk_id,
    chunks.c.minted_at,
    work_item_runs.c.routine_name,
    scopes.c.slug.label("scope_slug"),
    work_item_runs.c.mode,
)

# The three fact kinds `DeliveredSet.added_count`/`observed_count`/`gone_count` fold
# over — `finding_facts.finding_set_id` (src/blizzard/hub/store/schema.py) is written only for these on the
# delivery path; a person's own exit verb or `reopened` carries no `finding_set_id`.
_SET_COUNT_KINDS = ("add", "observed", "gone")


def _canonical_work_ref(stmts: ScopedStatements) -> ColumnElement[bool]:
    """A chunk that absorbed another's work refs (`GroupService.group`) carries more than one
    `chunk_work_refs` row — a run's own identity is always the lowest-id one, its mint-time row."""
    return chunk_work_refs.c.id == (
        stmts.select(chunk_work_refs, func.min(chunk_work_refs.c.id))
        .where(chunk_work_refs.c.chunk_id == chunks.c.chunk_id)
        .correlate(chunks)
        .scalar_subquery()
    )


def _identity_of(row: Row[Any]) -> RunIdentity:
    return RunIdentity(
        chunk_id=row.chunk_id,
        routine_name=row.routine_name,
        scope_slug=row.scope_slug,
        mode=RunMode(row.mode),
        minted_at=row.minted_at,
    )


class GardenRunStore:
    """Read-only garden-run adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def runs_in_window(self, *, since: datetime, until: datetime, project_id: str | None = None) -> list[RunDeliveries]:
        stmt = self._store.select(_run_identity_join(self._store), *_IDENTITY_COLUMNS).where(
            chunks.c.minted_at >= since, chunks.c.minted_at < until, _canonical_work_ref(self._store)
        )
        if project_id is not None:
            stmt = stmt.where(chunks.c.project_id == project_id)
        with self._store.read("runs_in_window") as conn:
            rows = conn.execute(stmt.order_by(chunks.c.minted_at.desc(), chunks.c.chunk_id.desc())).all()
            delivered_by_chunk = self._delivered_sets_by_chunk(conn, [row.chunk_id for row in rows])
        return [
            RunDeliveries(identity=_identity_of(row), delivered=delivered_by_chunk.get(row.chunk_id, []))
            for row in rows
        ]

    def run_identity(self, chunk_id: str) -> RunIdentity | None:
        with self._store.read("run_identity") as conn:
            row = conn.execute(
                self._store.select(_run_identity_join(self._store), *_IDENTITY_COLUMNS).where(
                    chunks.c.chunk_id == chunk_id, _canonical_work_ref(self._store)
                )
            ).one_or_none()
        return _identity_of(row) if row is not None else None

    def delivered_sets(self, chunk_id: str) -> list[DeliveredSetRaw]:
        with self._store.read("delivered_sets") as conn:
            rows = conn.execute(
                self._store.select(
                    self._store.scope_join(
                        finding_sets, artifacts, finding_sets.c.artifact_id == artifacts.c.artifact_id
                    ),
                    finding_sets.c.finding_set_id,
                    finding_sets.c.revisions,
                    finding_sets.c.measurement,
                    artifacts.c.data,
                )
                .where(finding_sets.c.chunk_id == chunk_id)
                .order_by(finding_sets.c.finding_set_id)
            ).all()
            add_ids_by_set = self._add_finding_ids_by_set(conn, [row.finding_set_id for row in rows])
            return [
                DeliveredSetRaw(
                    finding_set_id=row.finding_set_id,
                    revisions=json.loads(row.revisions),
                    measurement=row.measurement,
                    artifact_data=row.data,
                    add_finding_ids=add_ids_by_set[row.finding_set_id],
                )
                for row in rows
            ]

    def _delivered_sets_by_chunk(self, conn: Connection, chunk_ids: list[str]) -> dict[str, list[DeliveredSet]]:
        if not chunk_ids:
            return {}
        rows: list[Row[Any]] = []
        for batch in id_batches(chunk_ids):
            rows.extend(
                conn.execute(
                    self._store.select(
                        finding_sets,
                        finding_sets.c.chunk_id,
                        finding_sets.c.finding_set_id,
                        finding_sets.c.revisions,
                        finding_sets.c.measurement,
                    )
                    .where(finding_sets.c.chunk_id.in_(batch))
                    .order_by(finding_sets.c.finding_set_id)
                ).all()
            )
        rows.sort(key=lambda row: row.finding_set_id)
        counts_by_set = self._fact_counts_by_set(conn, [row.finding_set_id for row in rows])
        grouped: dict[str, list[DeliveredSet]] = defaultdict(list)
        for row in rows:
            counts = counts_by_set[row.finding_set_id]
            grouped[row.chunk_id].append(
                DeliveredSet(
                    finding_set_id=row.finding_set_id,
                    revisions=json.loads(row.revisions),
                    measurement=row.measurement,
                    added_count=counts["add"],
                    observed_count=counts["observed"],
                    gone_count=counts["gone"],
                )
            )
        return dict(grouped)

    @staticmethod
    def _fact_counts_by_set(conn: Connection, finding_set_ids: list[str]) -> dict[str, dict[str, int]]:
        """How many `add`/`observed`/`gone` facts each delivered set's own delivery
        recorded — one `GROUP BY` query for every id in `finding_set_ids`
        (index-backed on `ix_finding_facts_tenant_finding_set_id`), the `fact_summaries`
        shape, so the list read never issues one count query per row. `add`/`observed`/`gone`
        are named explicitly rather than left to whatever `finding_set_id` happens to
        carry, so an exit or `reopened` fact — which src/blizzard/hub/store/schema.py records with no
        `finding_set_id` at all — could never be miscounted here even if that changed."""
        counts: dict[str, dict[str, int]] = {
            finding_set_id: dict.fromkeys(_SET_COUNT_KINDS, 0) for finding_set_id in finding_set_ids
        }
        if not finding_set_ids:
            return counts
        for batch in id_batches(finding_set_ids):
            rows = conn.execute(
                statements_of(conn)
                .select(finding_facts, finding_facts.c.finding_set_id, finding_facts.c.kind, func.count().label("n"))
                .where(finding_facts.c.finding_set_id.in_(batch), finding_facts.c.kind.in_(_SET_COUNT_KINDS))
                .group_by(finding_facts.c.finding_set_id, finding_facts.c.kind)
            ).all()
            for row in rows:
                counts[row.finding_set_id][row.kind] = row.n
        return counts

    @staticmethod
    def _add_finding_ids_by_set(conn: Connection, finding_set_ids: list[str]) -> dict[str, list[str]]:
        """Every listed set's own `add` facts' finding ids, in insertion order, read in one
        query (index-backed on `ix_finding_facts_tenant_finding_set_id`). A set with no linked `add`
        fact maps to an empty list."""
        grouped: dict[str, list[str]] = {finding_set_id: [] for finding_set_id in finding_set_ids}
        if not finding_set_ids:
            return grouped
        rows = conn.execute(
            statements_of(conn)
            .select(finding_facts, finding_facts.c.finding_set_id, finding_facts.c.finding_id)
            .where(finding_facts.c.finding_set_id.in_(finding_set_ids), finding_facts.c.kind == "add")
            .order_by(finding_facts.c.finding_set_id, finding_facts.c.id.asc())
        ).all()
        for row in rows:
            grouped[row.finding_set_id].append(row.finding_id)
        return grouped


_garden_run_store_conforms: type[IReadGardenRunRepository] = GardenRunStore
