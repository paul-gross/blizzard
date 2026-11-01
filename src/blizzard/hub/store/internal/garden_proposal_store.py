"""SQLAlchemy adapter for the garden-proposal repository seam (package-private).
All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``).
The findings a proposal answers are a join over ``garden_proposal_findings``, never
a JSON column."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, Select, and_, func, or_
from sqlalchemy.engine import Connection

from blizzard.foundation.garden_proposals import (
    GardenProposalClosureKind,
    GardenProposalItemOutcome,
    GardenProposalOrigin,
)
from blizzard.foundation.store.batching import id_batches
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.domain.garden.proposals.model import (
    GardenProposal,
    GardenProposalCounts,
    GardenProposalEdit,
    GardenProposalFindingAlreadyLinkedError,
    GardenProposalFindingOtherProjectError,
    GardenProposalPage,
    GardenProposalState,
    IWriteGardenProposalRepository,
    RoutineProposalState,
    garden_proposal_state,
)
from blizzard.hub.domain.kernel.pagination import MalformedCursor, decode_cursor, encode_cursor
from blizzard.hub.domain.kernel.unset import UnsetType
from blizzard.hub.store.internal.finding_store import findings_outside_project
from blizzard.hub.store.schema import garden_proposal_closures, garden_proposal_findings, garden_proposals
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements, statements_of

#: `list_page`'s cursor, already total: `(created_at, proposal_id)`.
_CURSOR_ARITY = 2


def _encode_proposal_cursor(proposal: GardenProposal) -> str:
    return encode_cursor(iso_utc(proposal.created_at), proposal.proposal_id)


def _decode_proposal_cursor(cursor: str) -> tuple[datetime, str]:
    parts = decode_cursor(cursor)
    if len(parts) != _CURSOR_ARITY or not isinstance(parts[0], str) or not isinstance(parts[1], str):
        raise MalformedCursor(cursor)
    try:
        created_at = as_utc(datetime.fromisoformat(parts[0]))
    except ValueError:
        raise MalformedCursor(cursor) from None
    return created_at, parts[1]


def _proposal_page_stmt(
    stmts: ScopedStatements,
    after: tuple[datetime, str] | None,
    limit: int,
    *,
    origin: GardenProposalOrigin | None,
    project_id: str | None,
    routine_name: str | None = None,
    state: RoutineProposalState | None = None,
) -> Select[Any]:
    stmt = stmts.select(garden_proposals)
    c = garden_proposals.c
    if routine_name is not None:
        stmt = stmt.where(c.routine_name == routine_name)
    if state is not None and state is not RoutineProposalState.ALL:
        closure_exists = stmts.exists(garden_proposal_closures, garden_proposal_closures.c.proposal_id == c.proposal_id)
        stmt = stmt.where(~closure_exists if state is RoutineProposalState.OPEN else closure_exists)
    if origin is not None:
        stmt = stmt.where(c.origin == origin.value)
    if project_id is not None:
        stmt = stmt.where(c.project_id == project_id)
    if after is not None:
        created_at, proposal_id = after
        # The portable spelling of `(created_at, proposal_id) < (created_at, proposal_id)`
        # — row-value comparison support varies by backend (`bzh:sql-portable`).
        stmt = stmt.where(or_(c.created_at < created_at, and_(c.created_at == created_at, c.proposal_id < proposal_id)))
    return stmt.order_by(garden_proposals.c.created_at.desc(), garden_proposals.c.proposal_id.desc()).limit(limit)


def _refuse_another_projects_findings(conn: Any, finding_ids: Sequence[str], project_id: str) -> None:
    """Raise :class:`GardenProposalFindingOtherProjectError` naming the first of `finding_ids` stored in a project
    other than `project_id` — the store's own backstop beneath the domain's check."""
    outside = findings_outside_project(conn, finding_ids, project_id)
    if outside:
        raise GardenProposalFindingOtherProjectError(outside[0])


class GardenProposalStore:
    """Read-write garden-proposal adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def create(
        self,
        proposal_id: str,
        *,
        origin: GardenProposalOrigin,
        project_id: str,
        routine_name: str | None,
        created_by: str | None = None,
        class_: str,
        title: str,
        body: str,
        findings: list[str],
        at: datetime,
    ) -> GardenProposal:
        with self._store.write("create") as conn:
            conn.execute(
                self._store.insert(garden_proposals).values(
                    proposal_id=proposal_id,
                    origin=origin.value,
                    project_id=project_id,
                    routine_name=routine_name,
                    created_by=created_by,
                    class_=class_,
                    title=title,
                    body=body,
                    created_at=at,
                )
            )
            if findings:
                _refuse_another_projects_findings(conn, findings, project_id)
                conn.execute(
                    self._store.insert(garden_proposal_findings),
                    [{"proposal_id": proposal_id, "finding_id": finding_id} for finding_id in findings],
                )
        return GardenProposal(
            proposal_id=proposal_id,
            origin=origin,
            routine_name=routine_name,
            created_by=created_by,
            class_=class_,
            title=title,
            body=body,
            created_at=at,
            findings=list(findings),
            project_id=project_id,
        )

    def is_closed(self, conn: Any, proposal_id: str) -> bool:
        """Whether `proposal_id` already carries a closure. Locks the proposal's own row
        before reading closures
        (`tests/test_garden_proposal_closure_race.py::test_is_closed_locks_the_proposal_row_before_reading_closures`)."""
        stmts = statements_of(conn)
        conn.execute(
            stmts.update(garden_proposals)
            .where(garden_proposals.c.proposal_id == proposal_id)
            .values(proposal_id=proposal_id)
        )
        already = conn.execute(
            stmts.select(garden_proposal_closures, garden_proposal_closures.c.id).where(
                garden_proposal_closures.c.proposal_id == proposal_id
            )
        ).first()
        return already is not None

    def edit(self, proposal_id: str, edit: GardenProposalEdit) -> GardenProposal | None:
        """Writes only the columns `edit` gives — an `UNSET` one stays out of the `SET`, so
        a concurrent edit of another column survives."""
        given = {
            column: value
            for column, value in (("title", edit.title), ("class_", edit.class_), ("body", edit.body))
            if not isinstance(value, UnsetType)
        }
        with self._store.write("edit") as conn:
            if self.is_closed(conn, proposal_id):
                return None
            if given:
                conn.execute(
                    self._store.update(garden_proposals)
                    .where(garden_proposals.c.proposal_id == proposal_id)
                    .values(given)
                )
            return self._reread(conn, proposal_id)

    def attach(self, proposal_id: str, finding_ids: Sequence[str]) -> GardenProposal | None:
        with self._store.write("attach") as conn:
            if self.is_closed(conn, proposal_id):
                return None
            linked = set(self._findings(conn, proposal_id))
            for finding_id in finding_ids:
                if finding_id in linked:
                    raise GardenProposalFindingAlreadyLinkedError(proposal_id, finding_id)
            row = conn.execute(
                self._store.select(garden_proposals).where(garden_proposals.c.proposal_id == proposal_id)
            ).one()
            if finding_ids:
                _refuse_another_projects_findings(conn, finding_ids, row.project_id)
                conn.execute(
                    self._store.insert(garden_proposal_findings),
                    [{"proposal_id": proposal_id, "finding_id": finding_id} for finding_id in finding_ids],
                )
            return self._of(row, self._findings(conn, proposal_id))

    def detach(self, proposal_id: str, finding_ids: Sequence[str]) -> GardenProposal | None:
        with self._store.write("detach") as conn:
            if self.is_closed(conn, proposal_id):
                return None
            if finding_ids:
                conn.execute(
                    self._store.delete(garden_proposal_findings).where(
                        garden_proposal_findings.c.proposal_id == proposal_id,
                        garden_proposal_findings.c.finding_id.in_(finding_ids),
                    )
                )
            return self._reread(conn, proposal_id)

    def _reread(self, conn: Connection, proposal_id: str) -> GardenProposal:
        """The proposal as it now stands — the tail `edit` and `detach` share."""
        row = conn.execute(
            self._store.select(garden_proposals).where(garden_proposals.c.proposal_id == proposal_id)
        ).one()
        return self._of(row, self._findings(conn, proposal_id))

    def get(self, proposal_id: str) -> GardenProposal | None:
        with self._store.read("get") as conn:
            row = conn.execute(
                self._store.select(garden_proposals).where(garden_proposals.c.proposal_id == proposal_id)
            ).one_or_none()
            if row is None:
                return None
            return self._of(row, self._findings(conn, proposal_id))

    def list_all(self) -> list[GardenProposal]:
        """Newest first — `proposal_id` (a chronologically sortable ULID) breaks the tie a
        one-instant batch delivery leaves in `created_at` alone."""
        with self._store.read("list_all") as conn:
            rows = conn.execute(
                self._store.select(garden_proposals).order_by(
                    garden_proposals.c.created_at.desc(), garden_proposals.c.proposal_id.desc()
                )
            ).all()
            findings = self._findings_for(conn, [row.proposal_id for row in rows])
            return [self._of(row, findings[row.proposal_id]) for row in rows]

    def list_page(
        self,
        *,
        cursor: str | None = None,
        limit: int,
        origin: GardenProposalOrigin | None = None,
        project_id: str | None = None,
        routine_name: str | None = None,
        state: RoutineProposalState | None = None,
    ) -> GardenProposalPage:
        """`list_all`'s bounded sibling — same total order, a SQL
        keyset window. `origin`, `routine_name` and `state` (a closure-state filter) narrow
        in SQL, inside the window, so the page's cost is set by `limit`. No
        post-read filter narrows a garden proposal the way findings' liveness does, so
        no top-up: one over-fetch-by-one window suffices."""
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        after = _decode_proposal_cursor(cursor) if cursor is not None else None
        with self._store.read("list_page") as conn:
            rows = conn.execute(
                _proposal_page_stmt(
                    self._store,
                    after,
                    limit + 1,
                    origin=origin,
                    project_id=project_id,
                    routine_name=routine_name,
                    state=state,
                )
            ).all()
            page_rows = rows[:limit]
            findings = self._findings_for(conn, [row.proposal_id for row in page_rows])
        proposals = [self._of(row, findings[row.proposal_id]) for row in page_rows]
        next_cursor = _encode_proposal_cursor(proposals[-1]) if len(rows) > limit else None
        return GardenProposalPage(proposals=proposals, next_cursor=next_cursor)

    def list_for_routine(
        self, project_id: str, routine_name: str, *, state: RoutineProposalState = RoutineProposalState.ALL
    ) -> list[GardenProposal]:
        """`list_all`'s routine-narrowed sibling — newest first, the same
        `created_at`/`proposal_id` tie-break."""
        stmt = self._store.select(garden_proposals).where(
            garden_proposals.c.project_id == project_id, garden_proposals.c.routine_name == routine_name
        )
        closure_exists = self._store.exists(
            garden_proposal_closures, garden_proposal_closures.c.proposal_id == garden_proposals.c.proposal_id
        )
        if state is RoutineProposalState.OPEN:
            stmt = stmt.where(~closure_exists)
        elif state is RoutineProposalState.CLOSED:
            stmt = stmt.where(closure_exists)
        stmt = stmt.order_by(garden_proposals.c.created_at.desc(), garden_proposals.c.proposal_id.desc())
        with self._store.read("list_for_routine") as conn:
            rows = conn.execute(stmt).all()
            findings = self._findings_for(conn, [row.proposal_id for row in rows])
            return [self._of(row, findings[row.proposal_id]) for row in rows]

    def counts_by_class(
        self,
        *,
        since: datetime,
        until: datetime,
        routine_name: str | None = None,
        project_id: str | None = None,
        origin: GardenProposalOrigin | None = None,
    ) -> list[GardenProposalCounts]:
        """One `GROUP BY` query over a left join to `garden_proposal_closures` — a
        proposal with no closure row still groups in (as `NULL`/`NULL`, `OPEN`'s own
        shape), folded through :func:`garden_proposal_state` in Python. Grouped by
        `(project_id, origin, routine_name, class_)`: a routine named by both origins returns one row per origin, and
        a routine name two projects hold returns one row per project."""
        c = garden_proposals.c
        closures_c = garden_proposal_closures.c
        stmt = self._store.select(
            self._store.scope_join(
                garden_proposals, garden_proposal_closures, closures_c.proposal_id == c.proposal_id, isouter=True
            ),
            c.project_id,
            c.origin,
            c.routine_name,
            c.class_,
            closures_c.closure,
            closures_c.item_outcome,
            func.count().label("n"),
        ).where(c.created_at >= since, c.created_at < until)
        if project_id is not None:
            stmt = stmt.where(c.project_id == project_id)
        if routine_name is not None:
            stmt = stmt.where(c.routine_name == routine_name)
        if origin is not None:
            stmt = stmt.where(c.origin == origin.value)
        stmt = stmt.group_by(
            c.project_id, c.origin, c.routine_name, c.class_, closures_c.closure, closures_c.item_outcome
        )
        with self._store.read("counts_by_class") as conn:
            rows = conn.execute(stmt).all()
        accumulator: dict[tuple[str, GardenProposalOrigin, str | None, str], dict[GardenProposalState, int]] = {}
        for row in rows:
            key = (row.project_id, GardenProposalOrigin(row.origin), row.routine_name, row.class_)
            buckets = accumulator.setdefault(key, dict.fromkeys(GardenProposalState, 0))
            bucket = garden_proposal_state(
                GardenProposalClosureKind(row.closure) if row.closure is not None else None,
                GardenProposalItemOutcome(row.item_outcome) if row.item_outcome is not None else None,
            )
            buckets[bucket] += row.n
        return [
            GardenProposalCounts(
                project_id=project_id_,
                origin=origin_,
                routine_name=routine_name_,
                class_=class_,
                open=buckets[GardenProposalState.OPEN],
                passed=buckets[GardenProposalState.PASSED],
                accepted_with_item=buckets[GardenProposalState.ACCEPTED_MINTED],
                accepted_without_item=buckets[GardenProposalState.ACCEPTED_DECLINED],
            )
            for (project_id_, origin_, routine_name_, class_), buckets in sorted(
                accumulator.items(), key=lambda kv: (kv[0][1].value, kv[0][2] or "", kv[0][3], kv[0][0])
            )
        ]

    def _findings(self, conn: Connection, proposal_id: str) -> list[str]:
        rows = conn.execute(
            self._store.select(garden_proposal_findings, garden_proposal_findings.c.finding_id)
            .where(garden_proposal_findings.c.proposal_id == proposal_id)
            .order_by(garden_proposal_findings.c.finding_id)
        ).all()
        return [r.finding_id for r in rows]

    @staticmethod
    def _findings_for(conn: Connection, proposal_ids: Sequence[str]) -> dict[str, list[str]]:
        """`list_all`/`list_for_routine`'s bulk sibling to `_findings` — every listed
        proposal's findings in one batched read apiece, instead of one query per
        proposal. A proposal with no findings maps to an empty list, not an absent key."""
        result: dict[str, list[str]] = {proposal_id: [] for proposal_id in proposal_ids}
        for batch in id_batches(proposal_ids):
            rows = conn.execute(
                statements_of(conn)
                .select(
                    garden_proposal_findings,
                    garden_proposal_findings.c.proposal_id,
                    garden_proposal_findings.c.finding_id,
                )
                .where(garden_proposal_findings.c.proposal_id.in_(batch))
                .order_by(garden_proposal_findings.c.proposal_id, garden_proposal_findings.c.finding_id)
            ).all()
            for row in rows:
                result[row.proposal_id].append(row.finding_id)
        return result

    @staticmethod
    def _of(row: Row[Any], findings: list[str]) -> GardenProposal:
        return GardenProposal(
            proposal_id=row.proposal_id,
            origin=GardenProposalOrigin(row.origin),
            routine_name=row.routine_name,
            created_by=row.created_by,
            class_=row.class_,
            title=row.title,
            body=row.body,
            created_at=row.created_at,
            findings=findings,
            project_id=row.project_id,
        )


_garden_proposal_store_conforms: type[IWriteGardenProposalRepository] = GardenProposalStore
