"""SQLAlchemy adapter for the hub-owned work item repository seam (issue #357,
package-private). All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``).
Timestamps arrive already stamped (``bzh:injected-clock``).
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Connection, desc, insert, select, update
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.ids import WORK_ITEM_PREFIX, Id
from blizzard.hub.config import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.work import (
    Chunk,
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkItemAuthorKind,
    WorkItemClosure,
    WorkItemMaterializationOutcome,
    WorkItemRecord,
    WorkRef,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_store import (
    insert_chunk_rows,
    insert_materialization_row,
    insert_promote_rows,
    record_deleted_row,
)


class WorkItemStore:
    """The only implementation of :class:`~blizzard.hub.domain.work.IReadWorkItemRepository`
    / :class:`~blizzard.hub.domain.work.IWriteWorkItemRepository`, confined to
    ``store/internal/`` (``bzh:repository-split``)."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def get(self, source: str, ref: str) -> WorkItemRecord | None:
        with self._store.read("get") as conn:
            row = conn.execute(
                select(s.work_items).where(s.work_items.c.source == source, s.work_items.c.ref == ref)
            ).one_or_none()
        return self._record(row) if row is not None else None

    def list(self, source: str, *, limit: int = 200) -> list[WorkItemRecord]:
        with self._store.read("list") as conn:
            rows = conn.execute(
                select(s.work_items)
                .where(s.work_items.c.source == source)
                # created_at is not unique; the ULID work_item_id breaks the tie lexically by
                # creation, as graph_store.py's get_enabled_by_name does (bzh:sql-portable).
                .order_by(desc(s.work_items.c.created_at), desc(s.work_items.c.work_item_id))
                .limit(limit)
            ).all()
        return [self._record(row) for row in rows]

    def create_with_chunk(
        self,
        *,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        stated_priority: str | None,
        at: datetime,
        chunk: Chunk,
    ) -> WorkItemRecord:
        """Insert the item row and ``chunk``'s own rows on one ``engine.begin()``
        connection — the mechanism behind
        :meth:`~blizzard.hub.domain.work.IWriteWorkItemRepository.create_with_chunk`'s
        atomicity contract."""
        with self._store.write("create_with_chunk") as conn:
            work_item_id = self._insert_item(
                conn,
                source=pointer.source,
                ref=pointer.ref,
                title=title,
                body=body,
                author=author,
                stated_priority=stated_priority,
                at=at,
            )
            insert_chunk_rows(conn, chunk)
        return WorkItemRecord(
            work_item_id=work_item_id,
            source=pointer.source,
            ref=pointer.ref,
            title=title,
            body=body,
            author=author,
            stated_priority=stated_priority,
            created_at=at,
            edited_at=at,
        )

    def create_with_chunk_and_promote(
        self,
        *,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        routine_name: str,
        scope_slug: str,
        run_mode: str,
        at: datetime,
        chunk: Chunk,
        position: float,
    ) -> tuple[WorkItemRecord, int | None]:
        """:meth:`create_with_chunk` plus the promote-then-tail-stamp pair
        (:func:`~blizzard.hub.store.internal.chunk_store.insert_promote_rows`), on one
        ``engine.begin()`` connection — a routine run's own one-act mint (blizzard#392)."""
        with self._store.write("create_with_chunk_and_promote") as conn:
            work_item_id = self._insert_item(
                conn,
                source=pointer.source,
                ref=pointer.ref,
                title=title,
                body=body,
                author=author,
                stated_priority=None,
                at=at,
                routine_name=routine_name,
                scope_slug=scope_slug,
                run_mode=run_mode,
            )
            insert_chunk_rows(conn, chunk)
            promoted_id = insert_promote_rows(conn, chunk.chunk_id, position=position, at=at)
        record = WorkItemRecord(
            work_item_id=work_item_id,
            source=pointer.source,
            ref=pointer.ref,
            title=title,
            body=body,
            author=author,
            stated_priority=None,
            created_at=at,
            edited_at=at,
            routine_name=routine_name,
            scope_slug=scope_slug,
            run_mode=run_mode,
        )
        return record, promoted_id

    @staticmethod
    def _insert_item(
        conn: Connection,
        *,
        source: str,
        ref: str,
        title: str,
        body: str,
        author: WorkItemAuthor,
        stated_priority: str | None,
        at: datetime,
        routine_name: str | None = None,
        scope_slug: str | None = None,
        run_mode: str | None = None,
    ) -> str:
        """Insert one ``work_items`` row on ``conn``, open, and return its minted id.
        ``routine_name``/``scope_slug``/``run_mode`` are a routine run's own indexed
        values (blizzard#392) — ``None`` for every other item."""
        work_item_id = Id.mint_at(WORK_ITEM_PREFIX, at).value
        author_payload = (
            {"user_id": author.user_id}
            if author.kind is WorkItemAuthorKind.USER
            else {"runner_id": author.runner_id, "chunk_id": author.chunk_id, "node_name": author.node_name}
        )
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
                routine_name=routine_name,
                scope_slug=scope_slug,
                run_mode=run_mode,
            )
        )
        return work_item_id

    def edit(
        self, source: str, ref: str, *, title: str, body: str, stated_priority: str | None, at: datetime
    ) -> WorkItemRecord | None:
        with self._store.write("edit") as conn:
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
        with self._store.write("close") as conn:
            self._close_conn(conn, source, ref, closure=closure, at=at)
            row = conn.execute(
                select(s.work_items).where(s.work_items.c.source == source, s.work_items.c.ref == ref)
            ).one()
        return self._record(row)

    def delete_chunk_and_withdraw_hub_items(self, chunk: Chunk, *, by: str, at: datetime) -> int:
        """Insert ``chunk``'s ``chunk_deleted`` row and close every open ``hub:``-source
        item it holds as withdrawn, on one ``engine.begin()`` connection (issue #364)
        — mirrors :meth:`create_with_chunk`'s own atomicity shape. A ``forge:``-sourced
        pointer on the same chunk is left untouched. Returns the freshly-written
        ``chunk_deleted.id``."""
        with self._store.write("delete_chunk_and_withdraw_hub_items") as conn:
            deleted_id = record_deleted_row(conn, chunk.chunk_id, by=by, at=at)
            for pointer in chunk.work_refs:
                if pointer.source == RESERVED_HUB_SOURCE_NAME:
                    self._close_conn(conn, pointer.source, pointer.ref, closure=WorkItemClosure.WITHDRAWN, at=at)
        return deleted_id

    def materialize_create(
        self,
        *,
        proposal_id: str,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        stated_priority: str | None,
        at: datetime,
        chunk: Chunk,
    ) -> bool:
        """Mint the item, ``chunk``'s own rows, and ``proposal_id``'s ``created`` outcome
        fact on one ``engine.begin()`` connection (D8) — :meth:`create_with_chunk` plus
        the outcome row, checked first so an already-judged proposal mints nothing."""
        with self._store.write("materialize_create") as conn:
            if not insert_materialization_row(
                conn,
                proposal_id=proposal_id,
                outcome=WorkItemMaterializationOutcome.CREATED,
                pointer=pointer,
                reason=None,
                at=at,
            ):
                return False
            self._insert_item(
                conn,
                source=pointer.source,
                ref=pointer.ref,
                title=title,
                body=body,
                author=author,
                stated_priority=stated_priority,
                at=at,
            )
            insert_chunk_rows(conn, chunk)
        return True

    def materialize_update(self, *, proposal_id: str, source: str, ref: str, evidence: str, at: datetime) -> bool:
        """Append ``evidence`` to an open item's body, stamp ``edited_at``, and record
        ``proposal_id``'s ``updated`` outcome fact on one ``engine.begin()`` connection
        (D8). Returns ``False`` and writes nothing when already judged, or when the item
        is no longer open — the append is one SQL-level concatenation so no read-then-write
        gap can lose a concurrent edit."""
        with self._store.write("materialize_update") as conn:
            already = conn.execute(
                select(s.work_item_materializations.c.id).where(
                    s.work_item_materializations.c.proposal_id == proposal_id
                )
            ).first()
            if already is not None:
                return False
            result = conn.execute(
                update(s.work_items)
                .where(s.work_items.c.source == source, s.work_items.c.ref == ref, s.work_items.c.closed_at.is_(None))
                .values(body=s.work_items.c.body + "\n\n" + evidence, edited_at=at)
            )
            if result.rowcount == 0:
                return False
            insert_materialization_row(
                conn,
                proposal_id=proposal_id,
                outcome=WorkItemMaterializationOutcome.UPDATED,
                pointer=WorkRef(source=source, ref=ref),
                reason=None,
                at=at,
            )
        return True

    @staticmethod
    def _close_conn(conn: Connection, source: str, ref: str, *, closure: WorkItemClosure, at: datetime) -> None:
        """Close an open item on a caller-supplied ``conn`` — extracted from :meth:`close`
        so :meth:`delete_chunk_and_withdraw_hub_items` can fold the same write into its
        own transaction (issue #364). No rowcount check: closing an item already closed,
        or one that never existed, is a silent no-op here, exactly as :meth:`close` was
        before this extraction."""
        conn.execute(
            update(s.work_items)
            .where(s.work_items.c.source == source, s.work_items.c.ref == ref, s.work_items.c.closed_at.is_(None))
            .values(closed_at=at, closure=closure.value)
        )

    def allocate_ref(self, source: str) -> str:
        """The next ``ref`` for ``source``, from its ``work_item_sequence`` counter row.

        A source's first-ever allocation has no counter row yet: optimistically insert
        one naming ``ref=1`` in its own transaction; a losing concurrent first allocation
        gets ``IntegrityError`` on the shared primary key and falls through to the
        already-exists path below, which increments the now-present row and returns the
        new value via ``RETURNING`` — a single portable statement (``bzh:sql-portable``)
        that lets postgres's row lock serialize concurrent winners on an existing row."""
        try:
            with self._store.write("allocate_ref", expect=(IntegrityError,)) as conn:
                conn.execute(insert(s.work_item_sequence).values(source=source, next_ref=2))
            return "1"
        except IntegrityError:
            pass
        with self._store.write("allocate_ref") as conn:
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
            else WorkItemAuthor.fleet(
                runner_id=payload["runner_id"], chunk_id=payload["chunk_id"], node_name=payload["node_name"]
            )
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
            routine_name=row.routine_name,
            scope_slug=row.scope_slug,
            run_mode=row.run_mode,
        )


def _conforms_work_item_store(x: WorkItemStore) -> IWriteWorkItemRepository:
    return x
