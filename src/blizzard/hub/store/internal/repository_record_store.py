"""SQLAlchemy adapter for the repository record seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Retired derives
from the append-only ``repository_lifecycle_facts`` table, newest-fact-wins per name
(``bzh:facts-not-status``). Every write commits its change row in the same transaction."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    IWriteRepositoryRecordRepository,
    RepositoryCoordinateTaken,
    RepositoryFields,
    RepositoryNameTaken,
    RepositorySecretUnavailable,
)
from blizzard.hub.domain.config.work_sources import ConfigRevisionConflict
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.internal.secret_store import secret_unavailable
from blizzard.hub.store.schema import repositories, repository_lifecycle_facts


def _retirements(conn: Connection, names: list[str] | None = None) -> dict[str, datetime]:
    """When each currently-retired repository's newest retirement fact was set."""
    facts = repository_lifecycle_facts
    found: dict[str, datetime] = {}
    for batch in id_batches(names) if names is not None else [None]:
        for row in conn.execute(newest_retired_select(facts, facts.c.name, batch, facts.c.set_at)):
            found[row.name] = row.set_at
    return found


def _check_secret(conn: Connection, secret: str) -> None:
    retired = secret_unavailable(conn, secret)
    if retired is not None:
        raise RepositorySecretUnavailable(secret, retired=retired)


def _coordinate_holder(conn: Connection, record: ConfiguredRepository) -> str | None:
    f = record.fields
    row = conn.execute(
        select(repositories.c.name).where(
            repositories.c.forge_api_url == f.forge_api_url,
            repositories.c.owner == f.owner,
            repositories.c.repo == f.repo,
            repositories.c.name != record.name,
        )
    ).first()
    return None if row is None else row.name


def _coordinate_taken(record: ConfiguredRepository, holder: str) -> RepositoryCoordinateTaken:
    f = record.fields
    return RepositoryCoordinateTaken(f.forge_api_url, f.owner, f.repo, holder=holder)


def _columns(record: ConfiguredRepository) -> dict[str, object]:
    f = record.fields
    return {
        "forge_api_url": f.forge_api_url,
        "owner": f.owner,
        "repo": f.repo,
        "base_branch": f.base_branch,
        "secret_name": f.secret_name,
    }


def insert_record(conn: Connection, record: ConfiguredRepository, change: ConfigChange) -> None:
    """The create body, inside the caller's transaction. :class:`RepositorySecretUnavailable`,
    :class:`RepositoryNameTaken` or :class:`RepositoryCoordinateTaken` when refused."""
    _check_secret(conn, record.fields.secret_name)
    if conn.execute(select(repositories.c.name).where(repositories.c.name == record.name)).first():
        raise RepositoryNameTaken(record.name)
    holder = _coordinate_holder(conn, record)
    if holder is not None:
        raise _coordinate_taken(record, holder)
    conn.execute(
        insert(repositories).values(
            name=record.name,
            **_columns(record),
            revision=record.revision,
            created_at=record.created_at,
            created_by=record.created_by,
        )
    )
    append_change(conn, change)


def update_record(conn: Connection, record: ConfiguredRepository, from_revision: int, change: ConfigChange) -> None:
    """The compare-and-set edit body, inside the caller's transaction."""
    _check_secret(conn, record.fields.secret_name)
    holder = _coordinate_holder(conn, record)
    if holder is not None:
        raise _coordinate_taken(record, holder)
    moved = conn.execute(
        update(repositories)
        .where(repositories.c.name == record.name, repositories.c.revision == from_revision)
        .values(**_columns(record), revision=record.revision)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("repository", record.name, current=_revision(conn, record.name))
    append_change(conn, change)


def record_lifecycle_fact(
    conn: Connection,
    record: ConfiguredRepository,
    *,
    retired: bool,
    from_revision: int,
    at: datetime,
    by: str,
    change: ConfigChange,
) -> None:
    """The retire/enable body, inside the caller's transaction. Enabling re-checks the secret."""
    if not retired:
        _check_secret(conn, record.fields.secret_name)
    moved = conn.execute(
        update(repositories)
        .where(repositories.c.name == record.name, repositories.c.revision == from_revision)
        .values(revision=record.revision)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("repository", record.name, current=_revision(conn, record.name))
    conn.execute(insert(repository_lifecycle_facts).values(name=record.name, retired=retired, set_at=at, set_by=by))
    append_change(conn, change)


def _revision(conn: Connection, name: str) -> int:
    return conn.execute(select(repositories.c.revision).where(repositories.c.name == name)).scalar_one()


class RepositoryRecordStore:
    """Read-write repository adapter over the hub store."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def create(self, record: ConfiguredRepository, *, change: ConfigChange) -> ConfiguredRepository:
        try:
            with self._store.write(
                "create", expect=(RepositorySecretUnavailable, RepositoryNameTaken, RepositoryCoordinateTaken)
            ) as conn:
                insert_record(conn, record, change)
        except IntegrityError as exc:  # a concurrent create won the race
            raise RepositoryNameTaken(record.name) from exc
        return record

    def update(self, record: ConfiguredRepository, *, from_revision: int, change: ConfigChange) -> ConfiguredRepository:
        with self._store.write(
            "update", expect=(RepositorySecretUnavailable, RepositoryCoordinateTaken, ConfigRevisionConflict)
        ) as conn:
            update_record(conn, record, from_revision, change)
        return record

    def record_lifecycle(
        self,
        record: ConfiguredRepository,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
    ) -> ConfiguredRepository:
        with self._store.write(
            "record_lifecycle", expect=(RepositorySecretUnavailable, ConfigRevisionConflict)
        ) as conn:
            record_lifecycle_fact(
                conn, record, retired=retired, from_revision=from_revision, at=at, by=by, change=change
            )
        return record

    def get(self, name: str) -> ConfiguredRepository | None:
        return self.get_many([name]).get(name)

    def get_many(self, names: list[str]) -> dict[str, ConfiguredRepository]:
        if not names:
            return {}
        found: dict[str, ConfiguredRepository] = {}
        with self._store.read("get_many") as conn:
            retired = _retirements(conn, names)
            for batch in id_batches(names):
                rows = conn.execute(select(repositories).where(repositories.c.name.in_(batch))).all()
                found.update({row.name: self._of(row, retired_at=retired.get(row.name)) for row in rows})
        return found

    def list_all(self, *, include_retired: bool) -> list[ConfiguredRepository]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(select(repositories).order_by(repositories.c.name)).all()
            retired = _retirements(conn)
        return [
            self._of(row, retired_at=retired.get(row.name))
            for row in rows
            if include_retired or row.name not in retired
        ]

    @staticmethod
    def _of(row, *, retired_at: datetime | None) -> ConfiguredRepository:  # type: ignore[no-untyped-def]
        return ConfiguredRepository(
            name=row.name,
            fields=RepositoryFields(
                forge_api_url=row.forge_api_url,
                owner=row.owner,
                repo=row.repo,
                base_branch=row.base_branch,
                secret_name=row.secret_name,
            ),
            revision=row.revision,
            created_at=row.created_at,
            created_by=row.created_by,
            retired=retired_at is not None,
            retired_at=retired_at,
        )


def _conforms_repository_store(x: RepositoryRecordStore) -> IWriteRepositoryRecordRepository:
    return x
