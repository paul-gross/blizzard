"""SQLAlchemy adapter for the revisions read (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Column, Select, Table

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.revisions import ConfigRevisions, IReadConfigRevisions
from blizzard.hub.store.schema import repositories, secrets, work_sources
from blizzard.hub.store.scoped import ScopedConnections

#: Each referring kind's table and the column keying it: its id.
_REFERRING: dict[RecordKind, tuple[Table, str]] = {
    RecordKind.WORK_SOURCE: (work_sources, "work_source_id"),
    RecordKind.REPOSITORY: (repositories, "repository_id"),
}


class ConfigRevisionsStore:
    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def revisions(self, kind: RecordKind, key: str) -> ConfigRevisions | None:
        return self.revisions_for(kind, [key]).get(key)

    def revisions_for(self, kind: RecordKind, keys: Sequence[str]) -> dict[str, ConfigRevisions]:
        if not keys:
            return {}
        query, key_column = self._query(kind)
        found: dict[str, ConfigRevisions] = {}
        with self._store.read("revisions_for") as conn:
            for batch in id_batches(list(dict.fromkeys(keys))):
                for row in conn.execute(query.where(key_column.in_(batch))).all():
                    found[row.key] = ConfigRevisions(
                        revision=row.revision, secret_name=row.secret_name, secret_revision=row.sr
                    )
        return found

    def _query(self, kind: RecordKind) -> tuple[Select, Column[Any]]:  # type: ignore[type-arg]
        """The revisions select of ``kind``, unfiltered, and the column keying it."""
        if kind is RecordKind.SECRET:
            query = self._store.select(
                secrets,
                secrets.c.name.label("key"),
                secrets.c.revision,
                secrets.c.name.label("secret_name"),
                secrets.c.revision.label("sr"),
            )
            return query, secrets.c.name
        table, key_column = _REFERRING[kind]
        query = self._store.select(
            self._store.scope_join(
                table,
                secrets,
                (secrets.c.tenant_id == table.c.tenant_id) & (secrets.c.name == table.c.secret_name),
                isouter=True,
            ),
            table.c[key_column].label("key"),
            table.c.revision,
            table.c.secret_name,
            secrets.c.revision.label("sr"),
        )
        return query, table.c[key_column]


def _conforms(x: ConfigRevisionsStore) -> IReadConfigRevisions:
    return x
