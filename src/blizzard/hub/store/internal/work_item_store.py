"""SQLAlchemy adapter for the hub-owned work item repository seam (issue #357,
package-private). All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``).
Timestamps arrive already stamped (``bzh:injected-clock``).
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Engine, desc, insert, select, update
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.ids import WORK_ITEM_PREFIX, Id
from blizzard.hub.domain.work import (
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkItemAuthorKind,
    WorkItemClosure,
    WorkItemRecord,
)
from blizzard.hub.store import schema as s


class WorkItemStore:
    """The only implementation of :class:`~blizzard.hub.domain.work.IReadWorkItemRepository`
    / :class:`~blizzard.hub.domain.work.IWriteWorkItemRepository`, confined to
    ``store/internal/`` (``bzh:repository-split``)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, source: str, ref: str) -> WorkItemRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(s.work_items).where(s.work_items.c.source == source, s.work_items.c.ref == ref)
            ).one_or_none()
        return self._record(row) if row is not None else None

    def list(self, source: str, *, limit: int = 200) -> list[WorkItemRecord]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(s.work_items)
                .where(s.work_items.c.source == source)
                # (created_at, work_item_id) desc — created_at alone is not unique, and a
                # ULID work_item_id sorts lexically by creation, the same tiebreaker
                # graph_store.py's get_enabled_by_name uses (bzh:sql-portable).
                .order_by(desc(s.work_items.c.created_at), desc(s.work_items.c.work_item_id))
                .limit(limit)
            ).all()
        return [self._record(row) for row in rows]

    def create(
        self,
        *,
        source: str,
        title: str,
        body: str,
        author: WorkItemAuthor,
        stated_priority: str | None,
        at: datetime,
    ) -> WorkItemRecord:
        ref = self._next_ref(source)
        work_item_id = Id.mint_at(WORK_ITEM_PREFIX, at).value
        author_payload = {"user_id": author.user_id} if author.kind is WorkItemAuthorKind.USER else {}
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.work_items).values(
                    work_item_id=work_item_id,
                    source=source,
                    ref=ref,
                    title=title,
                    body=body,
                    author_kind=author.kind.value,
                    author_payload=json.dumps(author_payload),
                    stated_priority=stated_priority,
                    created_at=at,
                    edited_at=at,
                    closed_at=None,
                    closure=None,
                )
            )
        return WorkItemRecord(
            work_item_id=work_item_id,
            source=source,
            ref=ref,
            title=title,
            body=body,
            author=author,
            stated_priority=stated_priority,
            created_at=at,
            edited_at=at,
        )

    def edit(
        self, source: str, ref: str, *, title: str, body: str, stated_priority: str | None, at: datetime
    ) -> WorkItemRecord | None:
        with self._engine.begin() as conn:
            result = conn.execute(
                update(s.work_items)
                .where(s.work_items.c.source == source, s.work_items.c.ref == ref, s.work_items.c.closed_at.is_(None))
                .values(title=title, body=body, stated_priority=stated_priority, edited_at=at)
            )
            if result.rowcount == 0:
                return None
            row = conn.execute(
                select(s.work_items).where(s.work_items.c.source == source, s.work_items.c.ref == ref)
            ).one()
        return self._record(row)

    def close(self, source: str, ref: str, *, closure: WorkItemClosure, at: datetime) -> WorkItemRecord:
        with self._engine.begin() as conn:
            conn.execute(
                update(s.work_items)
                .where(s.work_items.c.source == source, s.work_items.c.ref == ref, s.work_items.c.closed_at.is_(None))
                .values(closed_at=at, closure=closure.value)
            )
            row = conn.execute(
                select(s.work_items).where(s.work_items.c.source == source, s.work_items.c.ref == ref)
            ).one()
        return self._record(row)

    def _next_ref(self, source: str) -> str:
        """The next ``ref`` for ``source``, from its ``work_item_sequence`` counter row.

        A source's first-ever allocation has no counter row yet: optimistically insert
        one naming ``ref=1`` in its own transaction; a losing concurrent first allocation
        gets ``IntegrityError`` on the shared primary key and falls through to the
        already-exists path below, which increments the now-present row and returns the
        new value via ``RETURNING`` — a single portable statement (``bzh:sql-portable``)
        that lets postgres's row lock serialize concurrent winners on an existing row.
        Allocation never reuses a ref; it may skip one on a crash between this call and
        the item insert it feeds, the same gap-tolerant contract a DB sequence carries."""
        try:
            with self._engine.begin() as conn:
                conn.execute(insert(s.work_item_sequence).values(source=source, next_ref=2))
            return "1"
        except IntegrityError:
            pass
        with self._engine.begin() as conn:
            row = conn.execute(
                update(s.work_item_sequence)
                .where(s.work_item_sequence.c.source == source)
                .values(next_ref=s.work_item_sequence.c.next_ref + 1)
                .returning(s.work_item_sequence.c.next_ref)
            ).one()
        return str(row.next_ref - 1)

    @staticmethod
    def _record(row) -> WorkItemRecord:  # type: ignore[no-untyped-def]
        payload = json.loads(row.author_payload)
        author_kind = WorkItemAuthorKind(row.author_kind)
        author = (
            WorkItemAuthor.user(payload["user_id"])
            if author_kind is WorkItemAuthorKind.USER
            else WorkItemAuthor.fleet()
        )
        return WorkItemRecord(
            work_item_id=row.work_item_id,
            source=row.source,
            ref=row.ref,
            title=row.title,
            body=row.body,
            author=author,
            stated_priority=row.stated_priority,
            created_at=row.created_at,
            edited_at=row.edited_at,
            closed_at=row.closed_at,
            closure=WorkItemClosure(row.closure) if row.closure is not None else None,
        )


def _conforms_work_item_store(x: WorkItemStore) -> IWriteWorkItemRepository:
    return x
