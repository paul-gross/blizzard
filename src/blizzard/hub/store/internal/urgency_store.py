"""SQLAlchemy adapters for the urgency and its order (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Slug uniqueness is checked by the urgency's
writers; ``uq_urgencies_tenant_slug`` backstops a concurrent create, its ``IntegrityError`` re-raised as
``UrgencySlugTaken``, never swallowed. Retired derives from the append-only ``urgency_lifecycle_facts`` and a place from
the append-only ``urgency_positions``, newest fact per urgency winning. Every write commits its change row in the same
transaction (``bzh:configured-record``); the icon's bytes are read only by :meth:`UrgencyStore.icon`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Row, func
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.project_colors import HexColor, ProjectColor
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.urgency.mark import Icon, IconMark, IconMediaType
from blizzard.hub.domain.urgency.model import MarkWrite, Urgency, UrgencyId, UrgencySlugTaken
from blizzard.hub.domain.urgency.order import Placed, PositionFact, UrgencyOrder, UrgencyOrderConflict
from blizzard.hub.domain.urgency.ports import IWriteUrgencyOrder, IWriteUrgencyRepository

# Appends the config audit record on the caller's connection, atomically with the write.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.newest_fact import newest_fact_select, newest_retired_select
from blizzard.hub.store.schema import urgencies, urgency_lifecycle_facts, urgency_positions
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_in

#: Every column a view reads — the icon's bytes aside.
_VIEW_COLUMNS = tuple(column for column in urgencies.c if column.name != "icon_data")


def _color(raw: str) -> ProjectColor | HexColor:
    try:
        return ProjectColor(raw)
    except ValueError:
        return raw


def _color_value(color: ProjectColor | HexColor) -> str:
    return color.value if isinstance(color, ProjectColor) else color


def _columns(urgency: Urgency, mark: MarkWrite) -> dict[str, object]:
    values: dict[str, object] = {
        "name": urgency.name,
        "color": _color_value(urgency.color),
        "emoji": urgency.emoji,
    }
    if mark.icon is not None:
        values.update(
            icon_media_type=mark.icon.media_type.value, icon_data=mark.icon.data, icon_digest=mark.icon.digest
        )
    elif not mark.keep:
        values.update(icon_media_type=None, icon_data=None, icon_digest=None)
    return values


def insert_urgency(
    conn: Connection, urgency: Urgency, *, mark: MarkWrite, position: float, change: ConfigChange
) -> None:
    """The create body, inside the caller's transaction: the row, its first position fact, and the change."""
    stmts = statements_of(conn)
    conn.execute(
        stmts.insert(urgencies).values(
            urgency_id=urgency.urgency_id,
            slug=urgency.slug,
            created_at=urgency.created_at,
            created_by=urgency.created_by,
            revision=urgency.revision,
            **_columns(urgency, mark),
        )
    )
    conn.execute(
        stmts.insert(urgency_positions).values(
            urgency_id=urgency.urgency_id, position=position, set_at=urgency.created_at, set_by=urgency.created_by
        )
    )
    append_change(conn, change)


def _move_revision(conn: Connection, urgency: Urgency, from_revision: int, **values: object) -> None:
    """Compare-and-set the revision from ``from_revision`` to ``urgency.revision``, writing ``values`` beside it;
    :class:`ConfigRevisionConflict` when the stored revision has moved."""
    stmts = statements_of(conn)
    moved = conn.execute(
        stmts.update(urgencies)
        .where(urgencies.c.urgency_id == urgency.urgency_id, urgencies.c.revision == from_revision)
        .values(revision=urgency.revision, **values)
    ).rowcount
    if moved != 1:
        current = conn.execute(
            stmts.select(urgencies, urgencies.c.revision).where(urgencies.c.urgency_id == urgency.urgency_id)
        ).scalar_one()
        raise ConfigRevisionConflict("urgency", urgency.slug, current=current)


class UrgencyStore:
    """Read-write urgency adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def create(
        self,
        urgency: Urgency,
        *,
        mark: MarkWrite,
        position: float,
        change: ConfigChange,
        transaction: Transaction | None = None,
    ) -> Urgency:
        try:
            with connection_in(self._store, transaction, "create_urgency", write=True) as conn:
                insert_urgency(conn, urgency, mark=mark, position=position, change=change)
        except IntegrityError as exc:
            # A concurrent create that took the slug first loses here, and reads as the domain's own refusal.
            if transaction is None and (holder := self.get_by_slug(urgency.slug)) is not None:
                raise UrgencySlugTaken(urgency.slug, retired=holder.retired) from exc
            raise
        return urgency

    def update(
        self,
        urgency: Urgency,
        *,
        mark: MarkWrite,
        from_revision: int,
        change: ConfigChange,
        transaction: Transaction | None = None,
    ) -> Urgency:
        with connection_in(self._store, transaction, "update_urgency", write=True) as conn:
            _move_revision(conn, urgency, from_revision, **_columns(urgency, mark))
            append_change(conn, change)
        return urgency

    def record_lifecycle(
        self,
        urgency: Urgency,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
        transaction: Transaction | None = None,
    ) -> Urgency:
        with connection_in(self._store, transaction, "record_urgency_lifecycle", write=True) as conn:
            _move_revision(conn, urgency, from_revision)
            conn.execute(
                statements_of(conn)
                .insert(urgency_lifecycle_facts)
                .values(urgency_id=urgency.urgency_id, retired=retired, set_at=at, set_by=by)
            )
            append_change(conn, change)
        return urgency

    def get(self, urgency_id: UrgencyId, *, transaction: Transaction | None = None) -> Urgency | None:
        return self._one(urgencies.c.urgency_id == urgency_id, "get_urgency", transaction)

    def get_by_slug(self, slug: str, *, transaction: Transaction | None = None) -> Urgency | None:
        return self._one(urgencies.c.slug == slug, "get_urgency_by_slug", transaction)

    def get_many(
        self, urgency_ids: Sequence[UrgencyId], *, transaction: Transaction | None = None
    ) -> dict[UrgencyId, Urgency]:
        found: dict[UrgencyId, Urgency] = {}
        with connection_in(self._store, transaction, "get_many_urgencies") as conn:
            for batch in id_batches(sorted(set(urgency_ids))):
                rows = conn.execute(
                    self._store.select(urgencies, *_VIEW_COLUMNS).where(urgencies.c.urgency_id.in_(batch))
                ).all()
                found.update((u.urgency_id, u) for u in _reconstituted(conn, rows))
        return found

    def list_all(self, *, transaction: Transaction | None = None) -> list[Urgency]:
        with connection_in(self._store, transaction, "list_urgencies") as conn:
            rows = conn.execute(self._store.select(urgencies, *_VIEW_COLUMNS).order_by(urgencies.c.slug)).all()
            return _reconstituted(conn, rows)

    def icon(self, urgency_id: UrgencyId) -> Icon | None:
        with self._store.read("urgency_icon") as conn:
            row = conn.execute(
                self._store.select(urgencies, urgencies.c.icon_media_type, urgencies.c.icon_data).where(
                    urgencies.c.urgency_id == urgency_id
                )
            ).one_or_none()
        if row is None or row.icon_data is None:
            return None
        return Icon(IconMediaType(row.icon_media_type), bytes(row.icon_data))

    def _one(self, where: ColumnElement[bool], operation: str, transaction: Transaction | None) -> Urgency | None:
        with connection_in(self._store, transaction, operation) as conn:
            row = conn.execute(self._store.select(urgencies, *_VIEW_COLUMNS).where(where)).one_or_none()
            if row is None:
                return None
            return _reconstituted(conn, [row])[0]


def _retired_ids(conn: Connection, ids: Sequence[str]) -> set[str]:
    stmts = statements_of(conn)
    retired: set[str] = set()
    for batch in id_batches(list(ids)):
        retired.update(
            conn.execute(
                newest_retired_select(stmts, urgency_lifecycle_facts, urgency_lifecycle_facts.c.urgency_id, batch)
            ).scalars()
        )
    return retired


def _reconstituted(conn: Connection, rows: Sequence[Row[Any]]) -> list[Urgency]:
    """Each urgency row as a whole urgency, its brake read in a bounded number of queries."""
    if not rows:
        return []
    retired = _retired_ids(conn, [row.urgency_id for row in rows])
    return [_urgency_of(row, row.urgency_id in retired) for row in rows]


def _urgency_of(row: Row[Any], retired: bool) -> Urgency:
    icon = None
    if row.icon_digest is not None:
        icon = IconMark(IconMediaType(row.icon_media_type), row.icon_digest)
    return Urgency(
        urgency_id=UrgencyId(row.urgency_id),
        slug=row.slug,
        name=row.name,
        color=_color(row.color),
        emoji=row.emoji,
        icon=icon,
        created_at=row.created_at,
        created_by=row.created_by,
        revision=row.revision,
        retired=retired,
    )


def _order_seq(conn: Connection) -> int:
    stmts = statements_of(conn)
    return conn.execute(
        stmts.select(urgency_positions, func.coalesce(func.max(urgency_positions.c.id), 0))
    ).scalar_one()


class UrgencyOrderStore:
    """Read-write adapter over the tenant's urgency order."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def order(self, *, transaction: Transaction | None = None) -> UrgencyOrder:
        """The sequence is read before the positions: a fact landing between the two leaves it stale, so a write
        planned on the positions is refused rather than landing on places it never read."""
        with connection_in(self._store, transaction, "urgency_order") as conn:
            stmts = statements_of(conn)
            seq = _order_seq(conn)
            rows = conn.execute(stmts.select(urgencies, urgencies.c.urgency_id, urgencies.c.slug)).all()
            newest = conn.execute(
                newest_fact_select(
                    stmts,
                    urgency_positions,
                    urgency_positions.c.urgency_id,
                    None,
                    urgency_positions.c.urgency_id,
                    urgency_positions.c.position,
                )
            ).all()
            positions: dict[str, float] = {row.urgency_id: row.position for row in newest}
            retired = set(
                conn.execute(
                    newest_retired_select(stmts, urgency_lifecycle_facts, urgency_lifecycle_facts.c.urgency_id)
                ).scalars()
            )
        placed = [
            Placed(UrgencyId(row.urgency_id), row.slug, positions[row.urgency_id], row.urgency_id in retired)
            for row in rows
            if row.urgency_id in positions
        ]
        return UrgencyOrder.of(placed, seq)

    def record_positions(
        self,
        facts: Sequence[PositionFact],
        *,
        from_seq: int,
        at: datetime,
        by: str,
        change: ConfigChange | None,
        transaction: Transaction | None = None,
    ) -> int:
        if not facts:
            return from_seq
        with connection_in(self._store, transaction, "record_urgency_positions", write=True) as conn:
            if _order_seq(conn) != from_seq:
                raise UrgencyOrderConflict(expected=from_seq)
            conn.execute(
                statements_of(conn).insert_rows(
                    urgency_positions,
                    [
                        {"urgency_id": fact.urgency_id, "position": fact.position, "set_at": at, "set_by": by}
                        for fact in facts
                    ],
                )
            )
            seq = _order_seq(conn)
            if change is not None:
                append_change(conn, replace(change, revision=seq))
        return seq


_urgency_store_conforms: type[IWriteUrgencyRepository] = UrgencyStore
_urgency_order_store_conforms: type[IWriteUrgencyOrder] = UrgencyOrderStore
