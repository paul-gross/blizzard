"""SQLAlchemy adapter for the finding repository seam (package-private, blizzard#390).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the
no-stored-column contract this reads over is `schema.py`'s own (D2-D4)."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, insert, select

from blizzard.hub.domain.findings import (
    FACT_KINDS,
    Finding,
    FindingFact,
    FindingSet,
    IWriteFindingRepository,
    IWriteFindingSetRepository,
    UnknownFactKindError,
    derive_liveness,
)
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import finding_facts, finding_sets, findings


class FindingStore:
    """Read-write finding adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def add(
        self,
        finding_id: str,
        *,
        routine_name: str,
        scope_slug: str,
        class_: str,
        locus: str,
        summary: str,
        introduced: str | None,
        at: datetime,
    ) -> Finding:
        with self._store.write("add") as conn:
            conn.execute(
                insert(findings).values(
                    finding_id=finding_id,
                    routine_name=routine_name,
                    scope_slug=scope_slug,
                    class_=class_,
                    locus=locus,
                    summary=summary,
                    introduced=introduced,
                )
            )
            conn.execute(insert(finding_facts).values(finding_id=finding_id, kind="add", recorded_at=at, note=None))
        return Finding(
            finding_id=finding_id,
            routine_name=routine_name,
            scope_slug=scope_slug,
            class_=class_,
            locus=locus,
            summary=summary,
            introduced=introduced,
            live=True,
            last_seen_at=at,
            observed_count=0,
        )

    def record_fact(self, finding_id: str, *, kind: str, at: datetime, note: str | None = None) -> None:
        if kind not in FACT_KINDS:
            raise UnknownFactKindError(kind)
        with self._store.write("record_fact") as conn:
            conn.execute(insert(finding_facts).values(finding_id=finding_id, kind=kind, recorded_at=at, note=note))

    def get(self, finding_id: str) -> Finding | None:
        with self._store.read("get") as conn:
            row = conn.execute(select(findings).where(findings.c.finding_id == finding_id)).one_or_none()
            if row is None:
                return None
            facts = self._facts(conn, finding_id)
        return self._of(row, facts)

    def list_for(self, routine_name: str, scope_slug: str, *, include_gone: bool = False) -> list[Finding]:
        """The pass's own bucket read (D3) — filtered on `ix_findings_routine_scope`,
        ordered by `finding_id` so every backend returns the same rows."""
        with self._store.read("list_for") as conn:
            rows = conn.execute(
                select(findings)
                .where(findings.c.routine_name == routine_name, findings.c.scope_slug == scope_slug)
                .order_by(findings.c.finding_id)
            ).all()
            facts_by_id = self._facts_for_many(conn, [row.finding_id for row in rows])
            result = [self._of(row, facts_by_id[row.finding_id]) for row in rows]
        return [f for f in result if include_gone or f.live]

    def list_for_routine(self, routine_name: str, *, include_gone: bool = False) -> list[Finding]:
        """Every finding live on `routine_name`, across every scope (blizzard#393 Phase 4)
        — `list_for`'s scope-narrowed sibling, minus the `scope_slug` filter."""
        with self._store.read("list_for_routine") as conn:
            rows = conn.execute(
                select(findings).where(findings.c.routine_name == routine_name).order_by(findings.c.finding_id)
            ).all()
            facts_by_id = self._facts_for_many(conn, [row.finding_id for row in rows])
            result = [self._of(row, facts_by_id[row.finding_id]) for row in rows]
        return [f for f in result if include_gone or f.live]

    def count_by_class(self, routine_name: str, class_: str) -> int:
        """How often `class_` recurs for `routine_name` — filtered on
        `ix_findings_routine_class`."""
        with self._store.read("count_by_class") as conn:
            return conn.execute(
                select(func.count())
                .select_from(findings)
                .where(findings.c.routine_name == routine_name, findings.c.class_ == class_)
            ).scalar_one()

    def _facts(self, conn, finding_id: str) -> list[FindingFact]:  # type: ignore[no-untyped-def]
        rows = conn.execute(
            select(finding_facts).where(finding_facts.c.finding_id == finding_id).order_by(finding_facts.c.id.asc())
        ).all()
        return [FindingFact(kind=r.kind, recorded_at=r.recorded_at, note=r.note) for r in rows]

    def _facts_for_many(self, conn, finding_ids: list[str]) -> dict[str, list[FindingFact]]:  # type: ignore[no-untyped-def]
        """One query for every id in `finding_ids` (index-backed on
        `ix_finding_facts_finding_id_id`), so a bucket read never issues one fact query
        per row."""
        grouped: dict[str, list[FindingFact]] = {finding_id: [] for finding_id in finding_ids}
        if not finding_ids:
            return grouped
        rows = conn.execute(
            select(finding_facts)
            .where(finding_facts.c.finding_id.in_(finding_ids))
            .order_by(finding_facts.c.finding_id, finding_facts.c.id.asc())
        ).all()
        for r in rows:
            grouped[r.finding_id].append(FindingFact(kind=r.kind, recorded_at=r.recorded_at, note=r.note))
        return grouped

    @staticmethod
    def _of(row, facts: list[FindingFact]) -> Finding:  # type: ignore[no-untyped-def]
        state = derive_liveness(facts)
        return Finding(
            finding_id=row.finding_id,
            routine_name=row.routine_name,
            scope_slug=row.scope_slug,
            class_=row.class_,
            locus=row.locus,
            summary=row.summary,
            introduced=row.introduced,
            live=state.live,
            last_seen_at=state.last_seen_at,
            observed_count=state.observed_count,
        )


def _conforms_finding_store(x: FindingStore) -> IWriteFindingRepository:
    return x


class FindingSetStore:
    """Read-write finding-set adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def create(
        self,
        finding_set_id: str,
        *,
        artifact_id: str,
        chunk_id: str,
        scope_slug: str,
        revisions: dict[str, str],
        measurement: str | None,
    ) -> FindingSet:
        with self._store.write("create") as conn:
            conn.execute(
                insert(finding_sets).values(
                    finding_set_id=finding_set_id,
                    artifact_id=artifact_id,
                    chunk_id=chunk_id,
                    scope_slug=scope_slug,
                    revisions=json.dumps(revisions),
                    measurement=measurement,
                )
            )
        return FindingSet(
            finding_set_id=finding_set_id,
            artifact_id=artifact_id,
            chunk_id=chunk_id,
            scope_slug=scope_slug,
            revisions=dict(revisions),
            measurement=measurement,
        )

    def get(self, finding_set_id: str) -> FindingSet | None:
        with self._store.read("get") as conn:
            row = conn.execute(
                select(finding_sets).where(finding_sets.c.finding_set_id == finding_set_id)
            ).one_or_none()
        return self._of(row) if row is not None else None

    def list_for_chunk(self, chunk_id: str) -> list[FindingSet]:
        """A run's own delivered sets — filtered on `ix_finding_sets_chunk_id`."""
        with self._store.read("list_for_chunk") as conn:
            rows = conn.execute(select(finding_sets).where(finding_sets.c.chunk_id == chunk_id)).all()
        return [self._of(row) for row in rows]

    @staticmethod
    def _of(row) -> FindingSet:  # type: ignore[no-untyped-def]
        return FindingSet(
            finding_set_id=row.finding_set_id,
            artifact_id=row.artifact_id,
            chunk_id=row.chunk_id,
            scope_slug=row.scope_slug,
            revisions=json.loads(row.revisions),
            measurement=row.measurement,
        )


def _conforms_finding_set_store(x: FindingSetStore) -> IWriteFindingSetRepository:
    return x
