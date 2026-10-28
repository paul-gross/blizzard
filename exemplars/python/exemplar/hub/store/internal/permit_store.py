"""SQLAlchemy adapter for the permit repository seams (package-private)."""

# exemplar: bzh:tenant-scoped-store, bzh:bulk-reconstitution, bzh:newest-per-key-read, bzh:page-bounded-read,
# bzh:store-exclusive-write, bzh:sql-portable.

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy import Select, and_, or_
from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.batching import id_batches
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.domain.kernel.pagination import MalformedCursor, decode_cursor, encode_cursor
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store.internal.newest_fact import newest_fact_select
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements, statements_of
from exemplar.hub.domain.permits.model import (
    ILockedPermitRead,
    IWritePermitRepository,
    Permit,
    PermitAlreadyRequested,
    PermitFact,
    PermitFactKind,
    PermitPage,
)
from exemplar.hub.store.schema import permit_facts, permits

_FACT_COLUMNS = (
    permit_facts.c.permit_id,
    permit_facts.c.kind,
    permit_facts.c.recorded_at,
    permit_facts.c.recorded_by,
    permit_facts.c.note,
)

#: ``list_page``'s cursor, already total: ``(requested_at, permit_id)``.
_CURSOR_ARITY = 2


@dataclass(frozen=True)
class LockedPermitTransaction:
    """The concrete read handle, reading on :attr:`conn`, the connection the permit's row was locked on."""

    conn: Connection
    permit_id: str

    def permit(self) -> Permit:
        return _permits_on(self.conn, [self.permit_id])[self.permit_id]


class _LockedConnection(Protocol):
    conn: Connection


def conn_of(held: ILockedPermitRead) -> Connection:
    """The connection a domain-held :class:`ILockedPermitRead` was opened on."""
    return cast(_LockedConnection, held).conn


class PermitStore:
    """Read-write permit adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def get(self, permit_id: str) -> Permit | None:
        return self.get_many([permit_id]).get(permit_id)

    def get_many(self, permit_ids: Sequence[str]) -> dict[str, Permit]:
        if not permit_ids:
            return {}
        with self._store.read("get_many") as conn:
            return _permits_on(conn, permit_ids)

    def list_page(self, *, project: ProjectId | None, holder: str | None, limit: int, cursor: str | None) -> PermitPage:
        after = _decode_permit_cursor(cursor) if cursor is not None else None
        with self._store.read("list_page") as conn:
            rows = conn.execute(_permit_page_stmt(self._store, after, limit + 1, project=project, holder=holder)).all()
            page_rows = rows[:limit]
            latest = _latest_on(conn, [row.permit_id for row in page_rows])
        page = tuple(_permit_of(row, latest.get(row.permit_id)) for row in page_rows)
        next_cursor = _encode_permit_cursor(page[-1]) if len(rows) > limit else None
        return PermitPage(permits=page, next_cursor=next_cursor)

    def create(self, permit: Permit) -> None:
        try:
            with self._store.write("create", expect=(IntegrityError,)) as conn:
                conn.execute(
                    self._store.insert(permits).values(
                        permit_id=permit.permit_id,
                        project_id=permit.project_id,
                        holder=permit.holder,
                        capability=permit.capability,
                        requested_at=permit.requested_at,
                        requested_by=permit.requested_by,
                    )
                )
        except IntegrityError as exc:  # a duplicate of the project's holder and capability
            raise PermitAlreadyRequested(permit.holder, capability=permit.capability) from exc

    @contextmanager
    def locked(self, permit_id: str) -> Generator[ILockedPermitRead, None, None]:
        with self._store.write("locked") as conn:
            conn.execute(
                self._store.update(permits).where(permits.c.permit_id == permit_id).values(permit_id=permit_id)
            )
            yield LockedPermitTransaction(conn, permit_id)

    def record_fact_locked(self, held: ILockedPermitRead, fact: PermitFact) -> None:
        conn_of(held).execute(
            self._store.insert(permit_facts).values(
                permit_id=fact.permit_id,
                kind=fact.kind.value,
                recorded_at=fact.at,
                recorded_by=fact.by,
                note=fact.note,
            )
        )


def _permits_on(conn: Connection, permit_ids: Sequence[str]) -> dict[str, Permit]:
    """The permits under ``permit_ids`` read on ``conn``, keyed by id; an unknown id is dropped."""
    stmts = statements_of(conn)
    found: dict[str, Permit] = {}
    for batch in id_batches(permit_ids):
        rows = conn.execute(stmts.select(permits).where(permits.c.permit_id.in_(batch))).all()
        latest = _latest_on(conn, batch)
        found.update({row.permit_id: _permit_of(row, latest.get(row.permit_id)) for row in rows})
    return found


def _latest_on(conn: Connection, permit_ids: Sequence[str]) -> dict[str, PermitFact]:
    stmts = statements_of(conn)
    latest: dict[str, PermitFact] = {}
    for batch in id_batches(permit_ids):
        select = newest_fact_select(stmts, permit_facts, permit_facts.c.permit_id, batch, *_FACT_COLUMNS)
        latest.update({row.permit_id: _fact_of(row) for row in conn.execute(select)})
    return latest


def _permit_page_stmt(
    stmts: ScopedStatements,
    after: tuple[datetime, str] | None,
    limit: int,
    *,
    project: ProjectId | None,
    holder: str | None,
) -> Select[Any]:
    c = permits.c
    stmt = stmts.select(permits)
    if project is not None:
        stmt = stmt.where(c.project_id == project)
    if holder is not None:
        stmt = stmt.where(c.holder == holder)
    if after is not None:
        requested_at, permit_id = after
        # The portable spelling of ``(requested_at, permit_id) < (:requested_at, :permit_id)``.
        stmt = stmt.where(
            or_(c.requested_at < requested_at, and_(c.requested_at == requested_at, c.permit_id < permit_id))
        )
    return stmt.order_by(c.requested_at.desc(), c.permit_id.desc()).limit(limit)


def _encode_permit_cursor(permit: Permit) -> str:
    return encode_cursor(iso_utc(permit.requested_at), permit.permit_id)


def _decode_permit_cursor(cursor: str) -> tuple[datetime, str]:
    parts = decode_cursor(cursor)
    if len(parts) != _CURSOR_ARITY or not isinstance(parts[0], str) or not isinstance(parts[1], str):
        raise MalformedCursor(cursor)
    try:
        requested_at = as_utc(datetime.fromisoformat(parts[0]))
    except ValueError:
        raise MalformedCursor(cursor) from None
    return requested_at, parts[1]


def _permit_of(row: Row[Any], latest: PermitFact | None) -> Permit:
    return Permit(
        permit_id=row.permit_id,
        project_id=ProjectId(row.project_id),
        holder=row.holder,
        capability=row.capability,
        requested_at=row.requested_at,
        requested_by=row.requested_by,
        latest=latest,
    )


def _fact_of(row: Row[Any]) -> PermitFact:
    return PermitFact(
        permit_id=row.permit_id,
        kind=PermitFactKind(row.kind),
        at=row.recorded_at,
        by=row.recorded_by,
        note=row.note,
    )


_permit_store_conforms: type[IWritePermitRepository] = PermitStore
