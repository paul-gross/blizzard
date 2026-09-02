"""SQLAlchemy adapter for the garden-run read seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). The window
itself is bound in SQL and the list is ordered by an explicit ``ORDER BY`` on
``chunks.minted_at`` (never incidental row order, ``bzh:sql-portable``); outcome
derivation is left entirely to `src/blizzard/hub/domain/garden_run.py`, which reads the
chunk's own facts through its own seams."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select

from blizzard.hub.domain.garden_run import (
    DeliveredSet,
    DeliveredSetRaw,
    IReadGardenRunRepository,
    RunIdentity,
    RunRecord,
)
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import (
    artifacts,
    chunk_work_refs,
    chunks,
    finding_facts,
    finding_sets,
    work_item_runs,
    work_items,
)

# The chunk -> chunk_work_refs -> work_items -> work_item_runs join every read here
# starts from — a run's own identity, minted once at `RunService.run`'s own act.
_RUN_IDENTITY_JOIN = (
    chunks.join(chunk_work_refs, chunk_work_refs.c.chunk_id == chunks.c.chunk_id)
    .join(
        work_items,
        (work_items.c.source == chunk_work_refs.c.source) & (work_items.c.ref == chunk_work_refs.c.ref),
    )
    .join(work_item_runs, work_item_runs.c.work_item_id == work_items.c.work_item_id)
)

_IDENTITY_COLUMNS = (
    chunks.c.chunk_id,
    chunks.c.minted_at,
    work_item_runs.c.routine_name,
    work_item_runs.c.scope_slug,
    work_item_runs.c.mode,
)


def _identity_of(row: object) -> RunIdentity:
    return RunIdentity(
        chunk_id=row.chunk_id,  # type: ignore[attr-defined]
        routine_name=row.routine_name,  # type: ignore[attr-defined]
        scope_slug=row.scope_slug,  # type: ignore[attr-defined]
        mode=row.mode,  # type: ignore[attr-defined]
        minted_at=row.minted_at,  # type: ignore[attr-defined]
    )


class GardenRunStore:
    """Read-only garden-run adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def runs_in_window(self, *, since: datetime, until: datetime) -> list[RunRecord]:
        with self._store.read("runs_in_window") as conn:
            rows = conn.execute(
                select(*_IDENTITY_COLUMNS)
                .select_from(_RUN_IDENTITY_JOIN)
                .where(chunks.c.minted_at >= since, chunks.c.minted_at < until)
                .order_by(chunks.c.minted_at.desc(), chunks.c.chunk_id.desc())
            ).all()
            delivered_by_chunk = self._delivered_sets_by_chunk(conn, [row.chunk_id for row in rows])
        return [
            RunRecord(identity=_identity_of(row), delivered=delivered_by_chunk.get(row.chunk_id, [])) for row in rows
        ]

    def run_identity(self, chunk_id: str) -> RunIdentity | None:
        with self._store.read("run_identity") as conn:
            row = conn.execute(
                select(*_IDENTITY_COLUMNS).select_from(_RUN_IDENTITY_JOIN).where(chunks.c.chunk_id == chunk_id)
            ).one_or_none()
        return _identity_of(row) if row is not None else None

    def delivered_sets(self, chunk_id: str) -> list[DeliveredSetRaw]:
        with self._store.read("delivered_sets") as conn:
            rows = conn.execute(
                select(
                    finding_sets.c.finding_set_id,
                    finding_sets.c.revisions,
                    finding_sets.c.measurement,
                    artifacts.c.data,
                )
                .select_from(finding_sets.join(artifacts, finding_sets.c.artifact_id == artifacts.c.artifact_id))
                .where(finding_sets.c.chunk_id == chunk_id)
                .order_by(finding_sets.c.finding_set_id)
            ).all()
            return [
                DeliveredSetRaw(
                    finding_set_id=row.finding_set_id,
                    revisions=json.loads(row.revisions),
                    measurement=row.measurement,
                    artifact_data=row.data,
                    add_finding_ids=self._add_finding_ids(conn, row.finding_set_id),
                )
                for row in rows
            ]

    def _delivered_sets_by_chunk(self, conn, chunk_ids: list[str]) -> dict[str, list[DeliveredSet]]:  # type: ignore[no-untyped-def]
        if not chunk_ids:
            return {}
        rows = conn.execute(
            select(
                finding_sets.c.chunk_id,
                finding_sets.c.finding_set_id,
                finding_sets.c.revisions,
                finding_sets.c.measurement,
            )
            .where(finding_sets.c.chunk_id.in_(chunk_ids))
            .order_by(finding_sets.c.finding_set_id)
        ).all()
        grouped: dict[str, list[DeliveredSet]] = defaultdict(list)
        for row in rows:
            grouped[row.chunk_id].append(
                DeliveredSet(
                    finding_set_id=row.finding_set_id,
                    revisions=json.loads(row.revisions),
                    measurement=row.measurement,
                )
            )
        return dict(grouped)

    @staticmethod
    def _add_finding_ids(conn, finding_set_id: str) -> list[str]:  # type: ignore[no-untyped-def]
        """The finding ids `finding_set_id`'s own `add` facts minted, in insertion
        order — positionally parallel to its artifact's `AddFindingOp` entries
        (`GardenDelivery.deliver` appends one fact per op in artifact order). A set
        predating the `finding_facts.finding_set_id` linkage (Phase 1) matches none."""
        return list(
            conn.execute(
                select(finding_facts.c.finding_id)
                .where(finding_facts.c.finding_set_id == finding_set_id, finding_facts.c.kind == "add")
                .order_by(finding_facts.c.id.asc())
            )
            .scalars()
            .all()
        )


def _conforms_garden_run_store(x: GardenRunStore) -> IReadGardenRunRepository:
    return x
