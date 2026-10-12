"""SQLAlchemy adapter for the revisions read (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``)."""

from __future__ import annotations

from sqlalchemy import Table

from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.revisions import ConfigRevisions, IReadConfigRevisions
from blizzard.hub.store.schema import repositories, secrets, work_sources
from blizzard.hub.store.scoped import ScopedConnections

#: Each referring kind's table and the column keying it: a work source by its name, a repository by its id.
_REFERRING: dict[RecordKind, tuple[Table, str]] = {
    RecordKind.WORK_SOURCE: (work_sources, "name"),
    RecordKind.REPOSITORY: (repositories, "repository_id"),
}


class ConfigRevisionsStore:
    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def revisions(self, kind: RecordKind, key: str) -> ConfigRevisions | None:
        if kind is RecordKind.SECRET:
            query = self._store.select(
                secrets, secrets.c.revision, secrets.c.name.label("secret_name"), secrets.c.revision.label("sr")
            )
            query = query.where(secrets.c.name == key)
        else:
            table, key_column = _REFERRING[kind]
            query = self._store.select(
                self._store.scope_join(
                    table,
                    secrets,
                    (secrets.c.tenant_id == table.c.tenant_id) & (secrets.c.name == table.c.secret_name),
                    isouter=True,
                ),
                table.c.revision,
                table.c.secret_name,
                secrets.c.revision.label("sr"),
            ).where(table.c[key_column] == key)
        with self._store.read("revisions") as conn:
            row = conn.execute(query).one_or_none()
        if row is None:
            return None
        return ConfigRevisions(revision=row.revision, secret_name=row.secret_name, secret_revision=row.sr)


def _conforms(x: ConfigRevisionsStore) -> IReadConfigRevisions:
    return x
