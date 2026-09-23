"""SQLAlchemy adapter for the garden-proposal repository seam (package-private,
blizzard#390). All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``).
The findings a proposal answers are a join over ``garden_proposal_findings`` (D7), never
a JSON column."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, insert, or_, select

from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.domain.garden_proposal_closure import (
    GardenProposalClosureKind,
    GardenProposalCountBucket,
    GardenProposalItemOutcome,
    classify_proposal_count_bucket,
)
from blizzard.hub.domain.garden_proposals import (
    GardenProposal,
    GardenProposalCounts,
    GardenProposalPage,
    IWriteGardenProposalRepository,
)
from blizzard.hub.domain.pagination import MalformedCursor, decode_cursor, encode_cursor
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.batching import id_batches
from blizzard.hub.store.schema import garden_proposal_closures, garden_proposal_findings, garden_proposals

#: `list_page`'s cursor, already total: `(created_at, proposal_id)` (blizzard#526 D4).
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


def _proposal_page_stmt(after: tuple[datetime, str] | None, limit: int) -> Select[Any]:
    stmt = select(garden_proposals)
    if after is not None:
        created_at, proposal_id = after
        c = garden_proposals.c
        # The portable spelling of `(created_at, proposal_id) < (created_at, proposal_id)`
        # — row-value comparison support varies by backend (`bzh:sql-portable`).
        stmt = stmt.where(or_(c.created_at < created_at, and_(c.created_at == created_at, c.proposal_id < proposal_id)))
    return stmt.order_by(garden_proposals.c.created_at.desc(), garden_proposals.c.proposal_id.desc()).limit(limit)


class GardenProposalStore:
    """Read-write garden-proposal adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def create(
        self,
        proposal_id: str,
        *,
        routine_name: str,
        class_: str,
        title: str,
        body: str,
        findings: list[str],
        at: datetime,
    ) -> GardenProposal:
        with self._store.write("create") as conn:
            conn.execute(
                insert(garden_proposals).values(
                    proposal_id=proposal_id,
                    routine_name=routine_name,
                    class_=class_,
                    title=title,
                    body=body,
                    created_at=at,
                )
            )
            conn.execute(
                insert(garden_proposal_findings),
                [{"proposal_id": proposal_id, "finding_id": finding_id} for finding_id in findings],
            )
        return GardenProposal(
            proposal_id=proposal_id,
            routine_name=routine_name,
            class_=class_,
            title=title,
            body=body,
            created_at=at,
            findings=list(findings),
        )

    def get(self, proposal_id: str) -> GardenProposal | None:
        with self._store.read("get") as conn:
            row = conn.execute(
                select(garden_proposals).where(garden_proposals.c.proposal_id == proposal_id)
            ).one_or_none()
            if row is None:
                return None
            return self._of(row, self._findings(conn, proposal_id))

    def list_all(self) -> list[GardenProposal]:
        """Newest first — `proposal_id` (a chronologically sortable ULID) breaks the tie a
        one-instant batch delivery leaves in `created_at` alone."""
        with self._store.read("list_all") as conn:
            rows = conn.execute(
                select(garden_proposals).order_by(
                    garden_proposals.c.created_at.desc(), garden_proposals.c.proposal_id.desc()
                )
            ).all()
            findings = self._findings_for(conn, [row.proposal_id for row in rows])
            return [self._of(row, findings[row.proposal_id]) for row in rows]

    def list_page(self, *, cursor: str | None = None, limit: int) -> GardenProposalPage:
        """`list_all`'s bounded sibling (blizzard#526 D4) — same total order, a SQL
        keyset window. No post-read filter narrows a garden proposal the way findings'
        liveness does, so no top-up: one over-fetch-by-one window suffices."""
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        after = _decode_proposal_cursor(cursor) if cursor is not None else None
        with self._store.read("list_page") as conn:
            rows = conn.execute(_proposal_page_stmt(after, limit + 1)).all()
            page_rows = rows[:limit]
            findings = self._findings_for(conn, [row.proposal_id for row in page_rows])
        proposals = [self._of(row, findings[row.proposal_id]) for row in page_rows]
        next_cursor = _encode_proposal_cursor(proposals[-1]) if len(rows) > limit else None
        return GardenProposalPage(proposals=proposals, next_cursor=next_cursor)

    def list_for_routine(self, routine_name: str) -> list[GardenProposal]:
        """`list_all`'s routine-narrowed sibling — newest first, the same
        `created_at`/`proposal_id` tie-break."""
        with self._store.read("list_for_routine") as conn:
            rows = conn.execute(
                select(garden_proposals)
                .where(garden_proposals.c.routine_name == routine_name)
                .order_by(garden_proposals.c.created_at.desc(), garden_proposals.c.proposal_id.desc())
            ).all()
            findings = self._findings_for(conn, [row.proposal_id for row in rows])
            return [self._of(row, findings[row.proposal_id]) for row in rows]

    def counts_by_class(
        self, *, since: datetime, until: datetime, routine_name: str | None = None
    ) -> list[GardenProposalCounts]:
        """One `GROUP BY` query over a left join to `garden_proposal_closures` — a
        proposal with no closure row still groups in (as `NULL`/`NULL`, `OPEN`'s own
        shape), folded through :func:`classify_proposal_count_bucket` in Python rather
        than a Python-side fold over ungrouped rows (`GardenRunStore._fact_counts_by_set`'s
        own shape)."""
        c = garden_proposals.c
        closures_c = garden_proposal_closures.c
        stmt = (
            select(c.routine_name, c.class_, closures_c.closure, closures_c.item_outcome, func.count().label("n"))
            .select_from(garden_proposals.outerjoin(garden_proposal_closures, closures_c.proposal_id == c.proposal_id))
            .where(c.created_at >= since, c.created_at < until)
        )
        if routine_name is not None:
            stmt = stmt.where(c.routine_name == routine_name)
        stmt = stmt.group_by(c.routine_name, c.class_, closures_c.closure, closures_c.item_outcome)
        with self._store.read("counts_by_class") as conn:
            rows = conn.execute(stmt).all()
        accumulator: dict[tuple[str, str], dict[GardenProposalCountBucket, int]] = {}
        for row in rows:
            key = (row.routine_name, row.class_)
            buckets = accumulator.setdefault(key, dict.fromkeys(GardenProposalCountBucket, 0))
            bucket = classify_proposal_count_bucket(
                GardenProposalClosureKind(row.closure) if row.closure is not None else None,
                GardenProposalItemOutcome(row.item_outcome) if row.item_outcome is not None else None,
            )
            buckets[bucket] += row.n
        return [
            GardenProposalCounts(
                routine_name=routine_name,
                class_=class_,
                open=buckets[GardenProposalCountBucket.OPEN],
                passed=buckets[GardenProposalCountBucket.PASSED],
                accepted_with_item=buckets[GardenProposalCountBucket.ACCEPTED_WITH_ITEM],
                accepted_without_item=buckets[GardenProposalCountBucket.ACCEPTED_WITHOUT_ITEM],
            )
            for (routine_name, class_), buckets in sorted(accumulator.items())
        ]

    def _findings(self, conn, proposal_id: str) -> list[str]:  # type: ignore[no-untyped-def]
        rows = conn.execute(
            select(garden_proposal_findings.c.finding_id)
            .where(garden_proposal_findings.c.proposal_id == proposal_id)
            .order_by(garden_proposal_findings.c.finding_id)
        ).all()
        return [r.finding_id for r in rows]

    @staticmethod
    def _findings_for(conn, proposal_ids: Sequence[str]) -> dict[str, list[str]]:  # type: ignore[no-untyped-def]
        """`list_all`/`list_for_routine`'s bulk sibling to `_findings` — every listed
        proposal's findings in one batched read apiece, instead of one query per
        proposal. A proposal with no findings maps to an empty list, not an absent key."""
        result: dict[str, list[str]] = {proposal_id: [] for proposal_id in proposal_ids}
        for batch in id_batches(proposal_ids):
            rows = conn.execute(
                select(garden_proposal_findings.c.proposal_id, garden_proposal_findings.c.finding_id)
                .where(garden_proposal_findings.c.proposal_id.in_(batch))
                .order_by(garden_proposal_findings.c.finding_id)
            ).all()
            for row in rows:
                result[row.proposal_id].append(row.finding_id)
        return result

    @staticmethod
    def _of(row, findings: list[str]) -> GardenProposal:  # type: ignore[no-untyped-def]
        return GardenProposal(
            proposal_id=row.proposal_id,
            routine_name=row.routine_name,
            class_=row.class_,
            title=row.title,
            body=row.body,
            created_at=row.created_at,
            findings=findings,
        )


def _conforms_garden_proposal_store(x: GardenProposalStore) -> IWriteGardenProposalRepository:
    return x
