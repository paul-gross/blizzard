"""SQLAlchemy adapter for the finding repository seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``); the
no-stored-column contract this reads over is `src/blizzard/hub/store/schema.py`'s own. A finding
and a finding set link their scope by ``scope_id`` (resolved within the tenant at the write) and
keep its slug beside it as the label every read here keys on."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Connection, Row, and_, case, desc, func, or_

from blizzard.foundation.findings import FindingState
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.garden.findings.model import (
    EXIT_KINDS,
    FACT_KINDS,
    LIVE_KINDS,
    FactEntry,
    Finding,
    FindingFact,
    FindingFactSummary,
    FindingPage,
    FindingSet,
    IWriteFindingRepository,
    IWriteFindingSetRepository,
    UnknownFactKindError,
    liveness_of,
    summarize_facts,
)
from blizzard.hub.domain.kernel.pagination import MalformedCursor, decode_cursor, encode_cursor
from blizzard.hub.store.internal.newest_fact import newer_row_exists
from blizzard.hub.store.internal.scope_store import scope_id_of
from blizzard.hub.store.schema import finding_facts, finding_sets, findings
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements, statements_of

#: `list_page`'s cursor: a plain `finding_id`, already total unlike chunks' `minted_at`.
_CURSOR_ARITY = 1


def _no_newest_fact_in(
    stmts: ScopedStatements, kinds_ok: Callable[[ColumnElement[str]], ColumnElement[bool]]
) -> ColumnElement[bool]:
    """True exactly when no fact of the finding both satisfies `kinds_ok` and is its newest.
    Newest is `(recorded_at, id)` descending, correlated to `findings.finding_id`. Each `EXISTS`
    is confined to the tenant on its own alias."""
    fact = finding_facts.alias("fact")
    newer = finding_facts.alias("newer")
    has_newer = stmts.exists(
        newer,
        newer.c.finding_id == fact.c.finding_id,
        or_(
            newer.c.recorded_at > fact.c.recorded_at,
            and_(newer.c.recorded_at == fact.c.recorded_at, newer.c.id > fact.c.id),
        ),
    )
    return ~stmts.exists(fact, fact.c.finding_id == findings.c.finding_id, kinds_ok(fact.c.kind), ~has_newer)


def _newest_fact_is_live(stmts: ScopedStatements) -> ColumnElement[bool]:
    """True exactly when no fact of the finding is both outside `LIVE_KINDS` and its newest —
    `liveness_of`'s answer, so a finding with no facts reads live."""
    return _no_newest_fact_in(stmts, lambda kind: kind.not_in(sorted(LIVE_KINDS)))


def _newest_fact_is_not_exited(stmts: ScopedStatements) -> ColumnElement[bool]:
    """True exactly when the finding's newest fact is not an `EXIT_KINDS` one — the
    complement of `Finding.exited`, under the same newest order as `liveness_of`."""
    return _no_newest_fact_in(stmts, lambda kind: kind.in_(sorted(EXIT_KINDS)))


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
        statements_of(conn)
        .update(findings)
        .where(findings.c.finding_id.in_(finding_ids))
        .values(finding_id=findings.c.finding_id)
    )


def moved_findings(conn: Connection, expect: Mapping[str, str]) -> list[str]:
    """The ids in `expect` whose state, re-derived under their row locks (`lock_findings`, taken
    in sorted order), is no longer the state they were expected in — the one derive-and-compare
    every guarded finding write shares."""
    guarded = sorted(expect)
    for batch in id_batches(guarded):
        lock_findings(conn, batch)
    current = fact_summaries(conn, guarded)
    return [fid for fid in guarded if liveness_of(current[fid]).state != expect[fid]]


def findings_outside_project(conn: Connection, finding_ids: Sequence[str], project_id: str) -> list[str]:
    """The ids among `finding_ids` whose stored finding belongs to a project other than `project_id`, sorted — the
    backstop every writer citing findings shares, read by primary key one `id_batches` batch at a time."""
    outside: list[str] = []
    for batch in id_batches(sorted(set(finding_ids))):
        outside += conn.execute(
            statements_of(conn)
            .select(findings, findings.c.finding_id)
            .where(findings.c.finding_id.in_(batch), findings.c.project_id != project_id)
        ).scalars()
    return sorted(outside)


def fact_summaries(conn: Connection, finding_ids: Sequence[str]) -> dict[str, FindingFactSummary]:
    """Each finding's bounded `FindingFactSummary`, two statements per `id_batches` batch
    (``bzh:bulk-reconstitution``, ``bzh:page-bounded-read``), each returning at most one row per
    finding however long its chain — index-backed on `ix_finding_facts_tenant_finding_id_id`.
    The newest fact is the `(recorded_at, id)` anti-join (``bzh:newest-per-key-read``); the
    aggregates are conditional `min`/`max`/`count` grouped by finding. What the newest kind means
    is `liveness_of`'s alone. A finding with no facts reads the empty summary."""
    summaries = {finding_id: summarize_facts([]) for finding_id in finding_ids}
    for batch in id_batches(list(finding_ids)):
        stmts = statements_of(conn)
        seen_at = case((finding_facts.c.kind.in_(("add", "observed")), finding_facts.c.recorded_at))
        observed = case((finding_facts.c.kind == "observed", 1), else_=0)
        aggregates = conn.execute(
            stmts.select(
                finding_facts,
                finding_facts.c.finding_id,
                func.min(seen_at).label("first_observed_at"),
                func.max(seen_at).label("last_seen_at"),
                func.sum(observed).label("observed_count"),
            )
            .where(finding_facts.c.finding_id.in_(batch))
            .group_by(finding_facts.c.finding_id)
        ).all()
        newest_by_finding = {
            row.finding_id: fact_of(row)
            for row in conn.execute(
                stmts.select(finding_facts)
                .where(finding_facts.c.finding_id.in_(batch))
                .where(~_newer_fact_exists(stmts))
            ).all()
        }
        for agg in aggregates:
            summaries[agg.finding_id] = FindingFactSummary(
                newest=newest_by_finding[agg.finding_id],
                first_observed_at=agg.first_observed_at,
                last_seen_at=agg.last_seen_at,
                observed_count=int(agg.observed_count),
            )
    return summaries


def _newer_fact_exists(stmts: ScopedStatements) -> ColumnElement[bool]:
    """True when a later fact (`(recorded_at, id)` order) of the same finding exists."""
    newer = finding_facts.alias("newer")
    return newer_row_exists(
        stmts,
        newer,
        partition=[(finding_facts.c.finding_id, newer.c.finding_id)],
        order=[(finding_facts.c.recorded_at, newer.c.recorded_at), (finding_facts.c.id, newer.c.id)],
    )


class FindingStore:
    """Read-write finding adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def add(
        self,
        finding_id: str,
        *,
        project_id: str,
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
                self._store.insert(findings).values(
                    finding_id=finding_id,
                    project_id=project_id,
                    routine_name=routine_name,
                    scope_id=scope_id_of(self._store, scope_slug, project_id),
                    scope_slug=scope_slug,
                    class_=class_,
                    locus=locus,
                    summary=summary,
                    introduced=introduced,
                    source="routine",
                )
            )
            conn.execute(
                self._store.insert(finding_facts).values(finding_id=finding_id, kind="add", recorded_at=at, note=None)
            )
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
            project_id=project_id,
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
        `tests/test_finding_store.py::test_record_facts_is_all_or_nothing`. ``expect`` is the
        port's compare-and-set (``src/blizzard/hub/domain/garden/findings/model.py``); each named
        finding is re-derived under its row lock first (`lock_findings`)."""
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
                self._store.insert(finding_facts),
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
            row = conn.execute(self._store.select(findings).where(findings.c.finding_id == finding_id)).one_or_none()
            if row is None:
                return None
            summary = fact_summaries(conn, [finding_id])[finding_id]
        return self._of(row, summary)

    def get_many(self, finding_ids: Sequence[str]) -> dict[str, Finding]:
        """`get`'s batched sibling (`bzh:bulk-reconstitution`) — one row query and one
        `fact_summaries` read per `id_batches` batch over `finding_ids`, so a bulk exit
        verb's read side never issues one query pair per row."""
        if not finding_ids:
            return {}
        result: dict[str, Finding] = {}
        with self._store.read("get_many") as conn:
            for batch in id_batches(finding_ids):
                rows = conn.execute(self._store.select(findings).where(findings.c.finding_id.in_(batch))).all()
                summaries = fact_summaries(conn, [row.finding_id for row in rows])
                result.update({row.finding_id: self._of(row, summaries[row.finding_id]) for row in rows})
        return result

    def get_with_facts(self, finding_id: str) -> tuple[Finding, list[FindingFact]] | None:
        with self._store.read("get_with_facts") as conn:
            row = conn.execute(self._store.select(findings).where(findings.c.finding_id == finding_id)).one_or_none()
            if row is None:
                return None
            facts = self._facts(conn, finding_id)
            finding = self._of(row, summarize_facts(facts))
        return finding, facts

    def list_for(
        self, project_id: str, routine_name: str, scope_slug: str, *, include_gone: bool = False
    ) -> list[Finding]:
        """The pass's own bucket read — filtered on `ix_findings_tenant_project_routine_scope`,
        ordered by `finding_id` so every backend returns the same rows."""
        with self._store.read("list_for") as conn:
            rows = conn.execute(
                self._store.select(findings)
                .where(
                    findings.c.project_id == project_id,
                    findings.c.routine_name == routine_name,
                    findings.c.scope_slug == scope_slug,
                )
                .order_by(findings.c.finding_id)
            ).all()
            summaries = fact_summaries(conn, [row.finding_id for row in rows])
            result = [self._of(row, summaries[row.finding_id]) for row in rows]
        return [f for f in result if include_gone or f.live]

    def list_for_routine(
        self, project_id: str, routine_name: str, *, include_gone: bool = False, exclude_exited: bool = False
    ) -> list[Finding]:
        """Every finding live on `routine_name`, across every scope
        — `list_for`'s scope-narrowed sibling, minus the `scope_slug` filter. `exclude_exited`
        drops an exited finding in the query itself, so its row and facts are never read."""
        stmt = (
            self._store.select(findings)
            .where(findings.c.project_id == project_id, findings.c.routine_name == routine_name)
            .order_by(findings.c.finding_id)
        )
        if exclude_exited:
            stmt = stmt.where(_newest_fact_is_not_exited(self._store))
        with self._store.read("list_for_routine") as conn:
            rows = conn.execute(stmt).all()
            summaries = fact_summaries(conn, [row.finding_id for row in rows])
            result = [self._of(row, summaries[row.finding_id]) for row in rows]
        return [f for f in result if include_gone or f.live]

    def list_across_routines(
        self, project_id: str, scope_slug: str | None = None, *, include_gone: bool = False
    ) -> list[Finding]:
        with self._store.read("list_across_routines") as conn:
            stmt = (
                self._store.select(findings).where(findings.c.project_id == project_id).order_by(findings.c.finding_id)
            )
            if scope_slug is not None:
                stmt = stmt.where(findings.c.scope_slug == scope_slug)
            rows = conn.execute(stmt).all()
            summaries = fact_summaries(conn, [row.finding_id for row in rows])
            result = [self._of(row, summaries[row.finding_id]) for row in rows]
        return [f for f in result if include_gone or f.live]

    def list_by_source(
        self,
        project_id: str,
        *,
        scope_slug: str,
        source: str,
        include_gone: bool = False,
        exclude_exited: bool = False,
    ) -> list[Finding]:
        """Filtered on `ix_findings_tenant_project_scope_source`; `exclude_exited` drops an
        exited finding in the query itself."""
        stmt = (
            self._store.select(findings)
            .where(
                findings.c.project_id == project_id,
                findings.c.scope_slug == scope_slug,
                findings.c.source == source,
            )
            .order_by(findings.c.finding_id)
        )
        if exclude_exited:
            stmt = stmt.where(_newest_fact_is_not_exited(self._store))
        with self._store.read("list_by_source") as conn:
            rows = conn.execute(stmt).all()
            summaries = fact_summaries(conn, [row.finding_id for row in rows])
            result = [self._of(row, summaries[row.finding_id]) for row in rows]
        return [f for f in result if include_gone or f.live]

    def list_page(
        self,
        *,
        project_id: str | None,
        routine_name: str | None,
        scope_slug: str | None,
        source: str | None = None,
        class_: str | None = None,
        state: FindingState | None = None,
        include_gone: bool = False,
        descending: bool = False,
        cursor: str | None = None,
        limit: int,
    ) -> FindingPage:
        """Keyset page whose filters precede `LIMIT limit+1` and fact reconstitution.
        The state predicate shares the fold's newest-fact order, including factless live
        rows; the skipped-finding cost is an indexed newest-fact probe."""
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        stmt = (
            self._store.select(findings)
            .order_by(desc(findings.c.finding_id) if descending else findings.c.finding_id)
            .limit(limit + 1)
        )
        if project_id is not None:
            stmt = stmt.where(findings.c.project_id == project_id)
        if routine_name is not None:
            stmt = stmt.where(findings.c.routine_name == routine_name)
        if scope_slug is not None:
            stmt = stmt.where(findings.c.scope_slug == scope_slug)
        if source is not None:
            stmt = stmt.where(findings.c.source == source)
        if class_ is not None:
            stmt = stmt.where(findings.c.class_ == class_)
        if cursor is not None:
            key = _decode_finding_cursor(cursor)
            stmt = stmt.where(findings.c.finding_id < key if descending else findings.c.finding_id > key)
        if state == FindingState.LIVE or (state is None and not include_gone):
            stmt = stmt.where(_newest_fact_is_live(self._store))
        elif state is not None:
            stmt = stmt.where(~_no_newest_fact_in(self._store, lambda kind: kind == state.value))
        with self._store.read("list_page") as conn:
            rows = conn.execute(stmt).all()
            summaries = fact_summaries(conn, [row.finding_id for row in rows[:limit]])
        page = [self._of(row, summaries[row.finding_id]) for row in rows[:limit]]
        next_cursor = _encode_finding_cursor(page[-1]) if len(rows) > limit else None
        return FindingPage(findings=page, next_cursor=next_cursor)

    def distinct_classes(
        self, *, project_id: str | None, routine_name: str | None, scope_slug: str | None
    ) -> list[str]:
        """All distinct class labels: the page's ids cannot supply options for unloaded classes."""
        stmt = self._store.select(findings, findings.c.class_).distinct().order_by(findings.c.class_)
        if project_id is not None:
            stmt = stmt.where(findings.c.project_id == project_id)
        if routine_name is not None:
            stmt = stmt.where(findings.c.routine_name == routine_name)
        if scope_slug is not None:
            stmt = stmt.where(findings.c.scope_slug == scope_slug)
        with self._store.read("distinct_classes") as conn:
            return list(conn.execute(stmt).scalars())

    def count_by_class(self, project_id: str, routine_name: str, class_: str) -> int:
        """How often `class_` recurs for `routine_name` — filtered on
        `ix_findings_tenant_project_routine_class`."""
        with self._store.read("count_by_class") as conn:
            return conn.execute(
                self._store.select(findings, func.count()).where(
                    findings.c.project_id == project_id,
                    findings.c.routine_name == routine_name,
                    findings.c.class_ == class_,
                )
            ).scalar_one()

    def has_delivery_for_proposal(self, proposal_id: str) -> bool:
        with self._store.read("has_delivery_for_proposal") as conn:
            row = conn.execute(
                self._store.select(finding_facts, finding_facts.c.id)
                .where(finding_facts.c.proposal_id == proposal_id)
                .limit(1)
            ).first()
        return row is not None

    def _facts(self, conn: Connection, finding_id: str) -> list[FindingFact]:
        rows = conn.execute(
            self._store.select(finding_facts)
            .where(finding_facts.c.finding_id == finding_id)
            .order_by(finding_facts.c.id.asc())
        ).all()
        return [fact_of(r) for r in rows]

    @staticmethod
    def _of(row: Row[Any], summary: FindingFactSummary) -> Finding:
        state = liveness_of(summary)
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
            project_id=row.project_id,
        )


def fact_of(row: Row[Any]) -> FindingFact:
    """Project a stored fact row into the finding domain."""
    return FindingFact(
        kind=row.kind,
        recorded_at=row.recorded_at,
        note=row.note,
        actor=row.actor,
        proposal_id=row.proposal_id,
        superseded_by=row.superseded_by,
    )


_finding_store_conforms: type[IWriteFindingRepository] = FindingStore


class FindingSetStore:
    """Read-write finding-set adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def create(
        self,
        finding_set_id: str,
        *,
        artifact_id: str,
        chunk_id: str,
        project_id: str,
        scope_slug: str,
        routine_name: str,
        revisions: dict[str, str],
        measurement: str | None,
    ) -> FindingSet:
        with self._store.write("create") as conn:
            conn.execute(
                self._store.insert(finding_sets).values(
                    finding_set_id=finding_set_id,
                    artifact_id=artifact_id,
                    chunk_id=chunk_id,
                    project_id=project_id,
                    scope_id=scope_id_of(self._store, scope_slug, project_id),
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
            project_id=project_id,
        )

    def get(self, finding_set_id: str) -> FindingSet | None:
        with self._store.read("get") as conn:
            row = conn.execute(
                self._store.select(finding_sets).where(finding_sets.c.finding_set_id == finding_set_id)
            ).one_or_none()
        return self._of(row) if row is not None else None

    def list_for_chunk(self, chunk_id: str) -> list[FindingSet]:
        """A run's own delivered sets — filtered on `ix_finding_sets_tenant_chunk_id`."""
        with self._store.read("list_for_chunk") as conn:
            rows = conn.execute(self._store.select(finding_sets).where(finding_sets.c.chunk_id == chunk_id)).all()
        return [self._of(row) for row in rows]

    def newest_for_routine_scope(self, project_id: str, routine_name: str, scope_slug: str) -> FindingSet | None:
        """The newest set for the pair, ordered by `finding_set_id` descending — a
        monotonic-in-mint-instant ULID, so every backend returns the same row."""
        with self._store.read("newest_for_routine_scope") as conn:
            row = conn.execute(
                self._store.select(finding_sets)
                .where(
                    finding_sets.c.project_id == project_id,
                    finding_sets.c.routine_name == routine_name,
                    finding_sets.c.scope_slug == scope_slug,
                )
                .order_by(desc(finding_sets.c.finding_set_id))
                .limit(1)
            ).one_or_none()
        return self._of(row) if row is not None else None

    def newest_by_scope_for_routine(self, project_id: str, routine_name: str) -> list[FindingSet]:
        """One row per scope, the newest by `finding_set_id` — a group-by-max join,
        portable across backends (`bzh:sql-portable`)."""
        newest_per_scope = (
            self._store.select(
                finding_sets, finding_sets.c.scope_slug, func.max(finding_sets.c.finding_set_id).label("finding_set_id")
            )
            .where(finding_sets.c.project_id == project_id, finding_sets.c.routine_name == routine_name)
            .group_by(finding_sets.c.scope_slug)
            .subquery()
        )
        with self._store.read("newest_by_scope_for_routine") as conn:
            rows = conn.execute(
                self._store.select(finding_sets).join(
                    newest_per_scope, finding_sets.c.finding_set_id == newest_per_scope.c.finding_set_id
                )
            ).all()
        return [self._of(row) for row in rows]

    @staticmethod
    def _of(row: Row[Any]) -> FindingSet:
        return FindingSet(
            finding_set_id=row.finding_set_id,
            artifact_id=row.artifact_id,
            chunk_id=row.chunk_id,
            scope_slug=row.scope_slug,
            routine_name=row.routine_name,
            revisions=json.loads(row.revisions),
            measurement=row.measurement,
            project_id=row.project_id,
        )


_finding_set_store_conforms: type[IWriteFindingSetRepository] = FindingSetStore
