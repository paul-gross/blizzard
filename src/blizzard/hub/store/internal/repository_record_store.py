"""SQLAlchemy adapter for the repository record seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Retired derives
from the append-only ``repository_lifecycle_facts`` table, newest-fact-wins per id
(``bzh:facts-not-status``). Every write commits its change row in the same transaction.

Every write runs in the caller's unit, which declares the keyed locks of the forge and the secret the repository
names; whether it may name them is the domain's judgment, not this adapter's."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.credential_naming import CredentialId
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    IWriteRepositoryRecordRepository,
    RepositoryCoordinateTaken,
    RepositoryFields,
    RepositoryId,
    RepositorySlug,
    RepositorySlugTaken,
    resolve_repository_reference,
)
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction

# Appends the config audit record on the caller's connection, atomically with the write.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.config_change_store import append_change

# Shares the forge read on the caller's connection.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.forge_record_store import standing_forge
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.schema import repositories, repository_former_slugs, repository_lifecycle_facts
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_for, connection_in

_SLUGS = SlugTables(repositories, repository_former_slugs, "repository_id", RepositorySlugTaken)


def retirements(conn: Connection, ids: list[RepositoryId] | None = None) -> dict[str, datetime]:
    """When each currently-retired repository's newest retirement fact was set, by repository id."""
    facts = repository_lifecycle_facts
    found: dict[str, datetime] = {}
    for batch in id_batches(ids) if ids is not None else [None]:
        for row in conn.execute(
            newest_retired_select(statements_of(conn), facts, facts.c.repository_id, batch, facts.c.set_at)
        ):
            found[row.repository_id] = row.set_at
    return found


def _coordinate_holder(conn: Connection, record: ConfiguredRepository) -> str | None:
    f = record.fields
    row = conn.execute(
        statements_of(conn)
        .select(repositories, repositories.c.slug)
        .where(
            repositories.c.forge_id == f.forge_id,
            repositories.c.owner == f.owner,
            repositories.c.repo == f.repo,
            repositories.c.repository_id != record.repository_id,
        )
    ).first()
    return None if row is None else row.slug


def _coordinate_taken(conn: Connection, record: ConfiguredRepository, holder: str) -> RepositoryCoordinateTaken:
    f = record.fields
    forge = standing_forge(conn, f.forge_id)
    return RepositoryCoordinateTaken(forge.label if forge else f.forge_id, f.owner, f.repo, holder=holder)


def _columns(record: ConfiguredRepository) -> dict[str, object]:
    f = record.fields
    return {
        "slug": record.slug,
        "name": record.name,
        "forge_id": f.forge_id,
        "owner": f.owner,
        "repo": f.repo,
        "base_branch": f.base_branch,
        "secret_name": f.secret_name,
        "landing_policy": f.landing_policy.value,
        "allow_no_ci": f.allow_no_ci,
        "park_timeout_hours": f.park_timeout_hours,
        "credential_id": f.credential_id,
    }


def insert_record(conn: Connection, record: ConfiguredRepository, change: ConfigChange) -> None:
    """The create body, inside the caller's transaction. :class:`RepositorySlugTaken` or
    :class:`RepositoryCoordinateTaken` when refused."""
    _SLUGS.claim(conn, record.repository_id, record.slug)
    holder = _coordinate_holder(conn, record)
    if holder is not None:
        raise _coordinate_taken(conn, record, holder)
    conn.execute(
        statements_of(conn)
        .insert(repositories)
        .values(
            repository_id=record.repository_id,
            **_columns(record),
            revision=record.revision,
            created_at=record.created_at,
            created_by=record.created_by,
        )
    )
    append_change(conn, change)


def update_record(
    conn: Connection, record: ConfiguredRepository, from_revision: int, left_slug: str | None, change: ConfigChange
) -> None:
    """The compare-and-set edit body, inside the caller's transaction, moving the slug when ``left_slug`` is set."""
    if left_slug is not None:
        _SLUGS.claim(conn, record.repository_id, record.slug)
    holder = _coordinate_holder(conn, record)
    if holder is not None:
        raise _coordinate_taken(conn, record, holder)
    moved = conn.execute(
        statements_of(conn)
        .update(repositories)
        .where(repositories.c.repository_id == record.repository_id, repositories.c.revision == from_revision)
        .values(**_columns(record), revision=record.revision)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("repository", record.repository_id, current=_revision(conn, record.repository_id))
    if left_slug is not None:
        _SLUGS.release(conn, record.repository_id, left_slug, left_at=change.recorded_at)
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
    """The retire/enable body, inside the caller's transaction."""
    moved = conn.execute(
        statements_of(conn)
        .update(repositories)
        .where(repositories.c.repository_id == record.repository_id, repositories.c.revision == from_revision)
        .values(revision=record.revision)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("repository", record.repository_id, current=_revision(conn, record.repository_id))
    conn.execute(
        statements_of(conn)
        .insert(repository_lifecycle_facts)
        .values(repository_id=record.repository_id, retired=retired, set_at=at, set_by=by)
    )
    append_change(conn, change)


def _lost_race(conn: Connection, record: ConfiguredRepository) -> RepositoryCoordinateTaken | RepositorySlugTaken:
    """The refusal a write that lost the coordinate or the slug to a concurrent one reads as, on ``conn``."""
    holder = _coordinate_holder(conn, record)
    return _coordinate_taken(conn, record, holder) if holder is not None else RepositorySlugTaken(record.slug)


def _revision(conn: Connection, repository_id: str) -> int:
    return conn.execute(
        statements_of(conn)
        .select(repositories, repositories.c.revision)
        .where(repositories.c.repository_id == repository_id)
    ).scalar_one()


class RepositoryRecordStore:
    """Read-write repository adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def create(
        self, record: ConfiguredRepository, *, change: ConfigChange, transaction: LockedTransaction
    ) -> ConfiguredRepository:
        conn = self._connection(transaction, record)
        try:
            with conn.begin_nested():
                insert_record(conn, record, change)
        except IntegrityError as exc:
            raise _lost_race(conn, record) from exc
        return record

    def update(
        self,
        record: ConfiguredRepository,
        *,
        from_revision: int,
        left_slug: str | None,
        change: ConfigChange,
        transaction: LockedTransaction,
    ) -> ConfiguredRepository:
        conn = self._connection(transaction, record)
        try:
            with conn.begin_nested():
                update_record(conn, record, from_revision, left_slug, change)
        except IntegrityError as exc:
            raise _lost_race(conn, record) from exc
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
        transaction: LockedTransaction,
    ) -> ConfiguredRepository:
        conn = self._connection(transaction, record)
        record_lifecycle_fact(conn, record, retired=retired, from_revision=from_revision, at=at, by=by, change=change)
        return record

    def _connection(self, transaction: LockedTransaction, record: ConfiguredRepository) -> Connection:
        """``transaction``'s connection, which must lock every key ``record`` names."""
        for key in record.locks():
            connection_for(self._store, transaction, key=key)
        return connection_for(self._store, transaction)

    def get(self, repository_id: RepositoryId) -> ConfiguredRepository | None:
        return self.get_many([repository_id]).get(repository_id)

    def get_many(self, repository_ids: list[RepositoryId]) -> dict[RepositoryId, ConfiguredRepository]:
        if not repository_ids:
            return {}
        found: dict[RepositoryId, ConfiguredRepository] = {}
        with self._store.read("get_many") as conn:
            retired = retirements(conn, repository_ids)
            for batch in id_batches(repository_ids):
                rows = conn.execute(
                    self._store.select(repositories).where(repositories.c.repository_id.in_(batch))
                ).all()
                found.update(
                    {
                        RepositoryId(row.repository_id): _of(row, retired_at=retired.get(row.repository_id))
                        for row in rows
                    }
                )
        return found

    def list_all(self, *, include_retired: bool) -> list[ConfiguredRepository]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(self._store.select(repositories).order_by(repositories.c.slug)).all()
            retired = retirements(conn)
        return [
            _of(row, retired_at=retired.get(row.repository_id))
            for row in rows
            if include_retired or row.repository_id not in retired
        ]

    def get_by_slug(self, slug: RepositorySlug) -> ConfiguredRepository | None:
        with self._store.read("get_by_slug") as conn:
            row = conn.execute(self._store.select(repositories).where(repositories.c.slug == slug.value)).first()
            return None if row is None else _reconstituted(conn, row)

    def get_by_former_slug(self, slug: RepositorySlug) -> ConfiguredRepository | None:
        left = self._store.scope_join(
            repository_former_slugs,
            repositories,
            repository_former_slugs.c.repository_id == repositories.c.repository_id,
        )
        with self._store.read("get_by_former_slug") as conn:
            row = conn.execute(
                self._store.select(left, *repositories.c).where(repository_former_slugs.c.slug == slug.value)
            ).first()
            return None if row is None else _reconstituted(conn, row)

    def resolve(self, value: str) -> ConfiguredRepository | None:
        return resolve_repository_reference(value, self)

    def naming_credential(
        self, credential_id: CredentialId, *, transaction: Transaction | None = None
    ) -> list[ConfiguredRepository]:
        with connection_in(self._store, transaction, "naming_credential") as conn:
            rows = conn.execute(
                statements_of(conn)
                .select(repositories)
                .where(repositories.c.credential_id == credential_id)
                .order_by(repositories.c.slug)
            ).all()
            retired = retirements(conn, [RepositoryId(row.repository_id) for row in rows]) if rows else {}
        return [_of(row, retired_at=retired.get(row.repository_id)) for row in rows]


def _reconstituted(conn: Connection, row: Row[Any]) -> ConfiguredRepository:
    return _of(row, retired_at=retirements(conn, [RepositoryId(row.repository_id)]).get(row.repository_id))


def _of(row: Row[Any], *, retired_at: datetime | None) -> ConfiguredRepository:
    return ConfiguredRepository(
        repository_id=RepositoryId(row.repository_id),
        slug=row.slug,
        name=row.name,
        fields=RepositoryFields(
            forge_id=ForgeId(row.forge_id),
            owner=row.owner,
            repo=row.repo,
            base_branch=row.base_branch,
            secret_name=row.secret_name,
            landing_policy=LandingPolicy(row.landing_policy),
            allow_no_ci=row.allow_no_ci,
            park_timeout_hours=row.park_timeout_hours,
            credential_id=None if row.credential_id is None else CredentialId(row.credential_id),
        ),
        revision=row.revision,
        created_at=row.created_at,
        created_by=row.created_by,
        retired=retired_at is not None,
        retired_at=retired_at,
    )


_repository_store_conforms: type[IWriteRepositoryRecordRepository] = RepositoryRecordStore
