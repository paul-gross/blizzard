"""Claiming and releasing a slug for every slugged kind alike (package-private).

A record that takes a slug is refused when another record of the tenant holds it as its current one, and the slug stops
being any record's former slug; a record that leaves a slug keeps answering to it through a former-slug row until
another record claims it. Both run inside the caller's transaction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Table
from sqlalchemy.engine import Connection

from blizzard.hub.store.scoped import statements_of


@dataclass(frozen=True)
class SlugTables:
    """A slugged kind's record table, its former-slug table, the id column both share, and its refusal."""

    records: Table
    former: Table
    id_column: str
    taken: Callable[[str], Exception]

    def claim(self, conn: Connection, record_id: str, slug: str) -> None:
        """``record_id`` takes ``slug``: :attr:`taken` when another record holds it as its current slug, and no
        record answers to it as a former slug from now on."""
        self.refuse_held(conn, record_id, slug)
        self._forget(conn, slug)

    def refuse_held(self, conn: Connection, record_id: str, slug: str) -> None:
        """:attr:`taken` when a record other than ``record_id`` holds ``slug`` as its current one."""
        records_id = self.records.c[self.id_column]
        holder = conn.execute(
            statements_of(conn)
            .select(self.records, records_id)
            .where(self.records.c.slug == slug, records_id != record_id)
        ).first()
        if holder is not None:
            raise self.taken(slug)

    def release(self, conn: Connection, record_id: str, left_slug: str, *, left_at: datetime) -> None:
        """``record_id`` left ``left_slug``, and answers to it as a former slug until another record claims it."""
        self._forget(conn, left_slug)
        conn.execute(
            statements_of(conn)
            .insert(self.former)
            .values({"slug": left_slug, self.id_column: record_id, "left_at": left_at})
        )

    def _forget(self, conn: Connection, slug: str) -> None:
        conn.execute(statements_of(conn).delete(self.former).where(self.former.c.slug == slug))
