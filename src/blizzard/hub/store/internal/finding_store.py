"""SQLAlchemy adapter for the finding repository seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the
no-stored-column contract this reads over is `schema.py`'s own."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, Connection, and_, desc, exists, func, insert, or_, select

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.garden.findings.model import (
    FACT_KINDS,
    LIVE_KINDS,
    FactEntry,
    Finding,
    FindingFact,
    FindingPage,
    FindingSet,
    IWriteFindingRepository,
    IWriteFindingSetRepository,
    UnknownFactKindError,
    derive_liveness,
)
from blizzard.hub.domain.kernel.pagination import MalformedCursor, decode_cursor, encode_cursor
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import finding_facts, finding_sets, findings

#: `list_page`'s cursor: a plain `finding_id`, already total unlike chunks' `minted_at`.
_CURSOR_ARITY = 1


def _newest_fact_is_live() -> ColumnElement[bool]:
    """True exactly when no fact of the finding is both outside `LIVE_KINDS` and its newest —
    `derive_liveness`'s answer, so a finding with no facts reads live. Newest is
    `(recorded_at, id)` descending, correlated to `findings.finding_id`."""
    fact = finding_facts.alias("fact")
    newer = finding_facts.alias("newer")
    has_newer = exists().where(
        newer.c.finding_id == fact.c.finding_id,
        or_(
            newer.c.recorded_at > fact.c.recorded_at,
            and_(newer.c.recorded_at == fact.c.recorded_at, newer.c.id > fact.c.id),
        ),
    )
    return ~exists().where(
        fact.c.finding_id == findings.c.finding_id,
        fact.c.kind.not_in(sorted(LIVE_KINDS)),
        ~has_newer,
    )


def _encode_finding_cursor(finding: Finding) -> str:
    return encode_cursor(finding.finding_id)


def _decode_finding_cursor(cursor: str) -> str:
    parts = decode_cursor(cursor)
    if len(parts) != _CURSOR_ARITY or not isinstance(parts[0], str):
        raise MalformedCursor(cursor)
    return parts[0]


def lock_findings(conn: Connection, finding_ids: Sequence[str]) -> None:
    """Take the write lock of each named finding row — a no-op ``UPDATE``, which must be the
    transaction's first statement, or follow only the chunk-row lock where one is taken
    (``bzh:store-exclusive-write``; ``FOR UPDATE`` renders nothing on SQLite)."""
    conn.execute(
        findings.update().where(findings.c.finding_id.in_(finding_ids)).values(finding_id=findings.c.finding_id)
    )


def moved_findings(conn: Connection, expect: Mapping[str, str]) -> list[str]:
    """The ids in `expect` whose state, re-derived under their row locks (`lock_findings`, taken
    in sorted order), is no longer the state they were expected in — the one derive-and-compare
    every guarded finding write shares."""
    guarded = sorted(expect)
    for batch in id_batches(guarded):
        lock_findings(conn, batch)
    current = facts_by_finding(conn, guarded)
    return [fid for fid in guarded if derive_liveness(current[fid]).state != expect[fid]]


def facts_by_finding(conn: Connection, finding_ids: list[str]) -> dict[str, list[FindingFact]]:
    """One query per `id_batches` batch over `finding_ids` (index-backed on
    `ix_finding_facts_finding_id_id`), each finding's facts oldest first — `list_across_routines`
    can hand this an unbounded id list, and one unbatched `IN (...)` would eventually exceed
    the driver's own per-statement bind-parameter ceiling."""
    grouped: dict[str, list[FindingFact]] = {finding_id: [] for finding_id in finding_ids}
    for batch in id_batches(finding_ids):
        rows = conn.execute(
            select(finding_facts)
            .where(finding_facts.c.finding_id.in_(batch))
            .order_by(finding_facts.c.finding_id, finding_facts.c.id.asc())
        ).all()
        for r in rows:
            grouped[r.finding_id].append(FindingStore._fact_of(r))
    return grouped


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
                    source="routine",
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
            introduced_at=None,
            first_observed_at=at,
            live=True,
            state="live",
            note=None,
            last_seen_at=at,
            observed_count=0,
            source="routine",
        )

    def record_fact(
        self,
        finding_id: str,
        *,
        kind: str,
        at: datetime,
        note: str | None = None,
        actor: str | None = None,
        proposal_id: str | None = None,
        superseded_by: str | None = None,
    ) -> None:
        self.record_facts(
            [
                FactEntry(
                    finding_id=finding_id,
                    kind=kind,
                    at=at,
                    note=note,
                    actor=actor,
                    proposal_id=proposal_id,
                    superseded_by=superseded_by,
                )
            ]
        )

    def record_facts(self, entries: Sequence[FactEntry], *, expect: Mapping[str, str] | None = None) -> list[str]:
        """All-or-nothing — pinned by
        `tests/test_finding_store.py::test_record_facts_is_all_or_nothing`. With ``expect``, each
        named finding's state is re-derived under its row lock first (`lock_findings`); any that moved off its
        expected state is returned and nothing is written."""
        for entry in entries:
            if entry.kind not in FACT_KINDS:
                raise UnknownFactKindError(entry.kind)
        if not entries:
            return []
        with self._store.write("record_facts") as conn:
            if expect:
                moved = moved_findings(conn, expect)
                if moved:
                    return moved
            conn.execute(
                insert(finding_facts),
                [
                    {
                        "finding_id": entry.finding_id,
                        "kind": entry.kind,
                        "recorded_at": entry.at,
                        "note": entry.note,
                        "actor": entry.actor,
                        "proposal_id": entry.proposal_id,
                        "superseded_by": entry.superseded_by,
                    }
                    for entry in entries
                ],
            )
        return []

    def get(self, finding_id: str) -> Finding | None:
        with self._store.read("get") as conn:
            row = conn.execute(select(findings).where(findings.c.finding_id == finding_id)).one_or_none()
            if row is None:
                return None
            facts = self._facts(conn, finding_id)
        return self._of(row, facts)

    def get_many(self, finding_ids: Sequence[str]) -> dict[str, Finding]:
        """`get`'s batched sibling (`bzh:bulk-reconstitution`) — one row query and one
        `_facts_for_many` query per `id_batches` batch over `finding_ids`, so a bulk exit
        verb's read side never issues one query pair per row."""
        if not finding_ids:
            return {}
        result: dict[str, Finding] = {}
        with self._store.read("get_many") as conn:
            for batch in id_batches(finding_ids):
                rows = conn.execute(select(findings).where(findings.c.finding_id.in_(batch))).all()
                facts_by_id = self._facts_for_many(conn, [row.finding_id for row in rows])
                result.update({row.finding_id: self._of(row, facts_by_id[row.finding_id]) for row in rows})
        return result

    def get_with_facts(self, finding_id: str) -> tuple[Finding, list[FindingFact]] | None:
        with self._store.read("get_with_facts") as conn:
            row = conn.execute(select(findings).where(findings.c.finding_id == finding_id)).one_or_none()
            if row is None:
                return None
            facts = self._facts(conn, finding_id)
            finding = self._of(row, facts)
        return finding, facts

    def list_for(self, routine_name: str, scope_slug: str, *, include_gone: bool = False) -> list[Finding]:
        """The pass's own bucket read — filtered on `ix_findings_routine_scope`,
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
        """Every finding live on `routine_name`, across every scope
        — `list_for`'s scope-narrowed sibling, minus the `scope_slug` filter."""
        with self._store.read("list_for_routine") as conn:
            rows = conn.execute(
                select(findings).where(findings.c.routine_name == routine_name).order_by(findings.c.finding_id)
            ).all()
            facts_by_id = self._facts_for_many(conn, [row.finding_id for row in rows])
            result = [self._of(row, facts_by_id[row.finding_id]) for row in rows]
        return [f for f in result if include_gone or f.live]

    def list_across_routines(self, scope_slug: str | None = None, *, include_gone: bool = False) -> list[Finding]:
        with self._store.read("list_across_routines") as conn:
            stmt = select(findings).order_by(findings.c.finding_id)
            if scope_slug is not None:
                stmt = stmt.where(findings.c.scope_slug == scope_slug)
            rows = conn.execute(stmt).all()
            facts_by_id = self._facts_for_many(conn, [row.finding_id for row in rows])
            result = [self._of(row, facts_by_id[row.finding_id]) for row in rows]
        return [f for f in result if include_gone or f.live]

    def list_by_source(self, *, scope_slug: str, source: str, include_gone: bool = False) -> list[Finding]:
        """Filtered on `ix_findings_scope_source`."""
        with self._store.read("list_by_source") as conn:
            rows = conn.execute(
                select(findings)
                .where(findings.c.scope_slug == scope_slug, findings.c.source == source)
                .order_by(findings.c.finding_id)
            ).all()
            facts_by_id = self._facts_for_many(conn, [row.finding_id for row in rows])
            result = [self._of(row, facts_by_id[row.finding_id]) for row in rows]
        return [f for f in result if include_gone or f.live]

    def list_page(
        self,
        *,
        routine_name: str | None,
        scope_slug: str | None,
        source: str | None = None,
        include_gone: bool = False,
        cursor: str | None = None,
        limit: int,
    ) -> FindingPage:
        """`list_for`/`list_for_routine`/`list_across_routines` unified into one bounded,
        keyset-paginated read. With `include_gone=False` the query itself drops every finding
        whose newest fact is not live, so `LIMIT limit+1` bounds the page: one page query plus
        one `_facts_for_many` read over the page's own ids. The only per-skipped-finding cost
        is the indexed newest-fact probe."""
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        stmt = select(findings).order_by(findings.c.finding_id).limit(limit + 1)
        if routine_name is not None:
            stmt = stmt.where(findings.c.routine_name == routine_name)
        if scope_slug is not None:
            stmt = stmt.where(findings.c.scope_slug == scope_slug)
        if source is not None:
            stmt = stmt.where(findings.c.source == source)
        if cursor is not None:
            stmt = stmt.where(findings.c.finding_id > _decode_finding_cursor(cursor))
        if not include_gone:
            stmt = stmt.where(_newest_fact_is_live())
        with self._store.read("list_page") as conn:
            rows = conn.execute(stmt).all()
            facts_by_id = self._facts_for_many(conn, [row.finding_id for row in rows[:limit]])
        page = [self._of(row, facts_by_id[row.finding_id]) for row in rows[:limit]]
        next_cursor = _encode_finding_cursor(page[-1]) if len(rows) > limit else None
        return FindingPage(findings=page, next_cursor=next_cursor)

    def count_by_class(self, routine_name: str, class_: str) -> int:
        """How often `class_` recurs for `routine_name` — filtered on
        `ix_findings_routine_class`."""
        with self._store.read("count_by_class") as conn:
            return conn.execute(
                select(func.count())
                .select_from(findings)
                .where(findings.c.routine_name == routine_name, findings.c.class_ == class_)
            ).scalar_one()

    def has_delivery_for_proposal(self, proposal_id: str) -> bool:
        with self._store.read("has_delivery_for_proposal") as conn:
            row = conn.execute(
                select(finding_facts.c.id).where(finding_facts.c.proposal_id == proposal_id).limit(1)
            ).first()
        return row is not None

    def _facts(self, conn, finding_id: str) -> list[FindingFact]:  # type: ignore[no-untyped-def]
        rows = conn.execute(
            select(finding_facts).where(finding_facts.c.finding_id == finding_id).order_by(finding_facts.c.id.asc())
        ).all()
        return [self._fact_of(r) for r in rows]

    @staticmethod
    def _facts_for_many(conn, finding_ids: list[str]) -> dict[str, list[FindingFact]]:  # type: ignore[no-untyped-def]
        return facts_by_finding(conn, finding_ids)

    @staticmethod
    def _fact_of(row) -> FindingFact:  # type: ignore[no-untyped-def]
        return FindingFact(
            kind=row.kind,
            recorded_at=row.recorded_at,
            note=row.note,
            actor=row.actor,
            proposal_id=row.proposal_id,
            superseded_by=row.superseded_by,
        )

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
            introduced_at=row.introduced_at,
            first_observed_at=state.first_observed_at,
            live=state.live,
            state=state.state,
            note=state.note,
            last_seen_at=state.last_seen_at,
            observed_count=state.observed_count,
            source=row.source,
            severity=row.severity,
            raised_by_chunk_id=row.raised_by_chunk_id,
            actor=state.actor,
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
        routine_name: str,
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
                    routine_name=routine_name,
                    revisions=json.dumps(revisions),
                    measurement=measurement,
                )
            )
        return FindingSet(
            finding_set_id=finding_set_id,
            artifact_id=artifact_id,
            chunk_id=chunk_id,
            scope_slug=scope_slug,
            routine_name=routine_name,
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

    def newest_for_routine_scope(self, routine_name: str, scope_slug: str) -> FindingSet | None:
        """The newest set for the pair, ordered by `finding_set_id` descending — a
        monotonic-in-mint-instant ULID, so every backend returns the same row."""
        with self._store.read("newest_for_routine_scope") as conn:
            row = conn.execute(
                select(finding_sets)
                .where(finding_sets.c.routine_name == routine_name, finding_sets.c.scope_slug == scope_slug)
                .order_by(desc(finding_sets.c.finding_set_id))
                .limit(1)
            ).one_or_none()
        return self._of(row) if row is not None else None

    def newest_by_scope_for_routine(self, routine_name: str) -> list[FindingSet]:
        """One row per scope, the newest by `finding_set_id` — a group-by-max join,
        portable across backends (`bzh:sql-portable`)."""
        newest_per_scope = (
            select(finding_sets.c.scope_slug, func.max(finding_sets.c.finding_set_id).label("finding_set_id"))
            .where(finding_sets.c.routine_name == routine_name)
            .group_by(finding_sets.c.scope_slug)
            .subquery()
        )
        with self._store.read("newest_by_scope_for_routine") as conn:
            rows = conn.execute(
                select(finding_sets).join(
                    newest_per_scope, finding_sets.c.finding_set_id == newest_per_scope.c.finding_set_id
                )
            ).all()
        return [self._of(row) for row in rows]

    @staticmethod
    def _of(row) -> FindingSet:  # type: ignore[no-untyped-def]
        return FindingSet(
            finding_set_id=row.finding_set_id,
            artifact_id=row.artifact_id,
            chunk_id=row.chunk_id,
            scope_slug=row.scope_slug,
            routine_name=row.routine_name,
            revisions=json.loads(row.revisions),
            measurement=row.measurement,
        )


def _conforms_finding_set_store(x: FindingSetStore) -> IWriteFindingSetRepository:
    return x
