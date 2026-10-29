"""SQLAlchemy adapter for the repository record seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Retired derives
from the append-only ``repository_lifecycle_facts`` table, newest-fact-wins per id
(``bzh:facts-not-status``). Every write commits its change row in the same transaction.

Every write takes the named forge's row lock after the tenant's, refuses a forge that is gone or retired, and stores the
forge's API origin in the deprecated ``forge_api_url`` column, which the coordinate constraint keys on."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forges import ForgeId, ForgeOriginTaken, ForgeSlugTaken, NewForge
from blizzard.hub.domain.config.landing_policies import LandingPolicy
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    IWriteRepositoryRecordRepository,
    RepositoryCoordinateTaken,
    RepositoryFields,
    RepositoryId,
    RepositorySecretUnavailable,
    RepositorySlug,
    RepositorySlugTaken,
    resolve_repository_reference,
)
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.forge_record_store import (
    StandingForge,
    insert_new_forge,
    referred_forge,
    standing_forge,
)
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.internal.secret_store import lock_secret_row, secret_unavailable
from blizzard.hub.store.schema import repositories, repository_former_slugs, repository_lifecycle_facts
from blizzard.hub.store.scoped import ScopedConnections, statements_of

#: What creating the forge a write names refuses when another forge holds its origin or slug.
_FORGE_REFUSALS = (ForgeOriginTaken, ForgeSlugTaken)

_SLUGS = SlugTables(repositories, repository_former_slugs, "repository_id", RepositorySlugTaken)


def _retirements(conn: Connection, ids: list[RepositoryId] | None = None) -> dict[str, datetime]:
    """When each currently-retired repository's newest retirement fact was set, by repository id."""
    facts = repository_lifecycle_facts
    found: dict[str, datetime] = {}
    for batch in id_batches(ids) if ids is not None else [None]:
        for row in conn.execute(
            newest_retired_select(statements_of(conn), facts, facts.c.repository_id, batch, facts.c.set_at)
        ):
            found[row.repository_id] = row.set_at
    return found


def _check_secret(conn: Connection, secret: str) -> None:
    lock_secret_row(conn, secret)
    retired = secret_unavailable(conn, secret)
    if retired is not None:
        raise RepositorySecretUnavailable(secret, retired=retired)


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


def _columns(record: ConfiguredRepository, forge: StandingForge) -> dict[str, object]:
    f = record.fields
    return {
        "slug": record.slug,
        "name": record.name,
        "forge_id": f.forge_id,
        "forge_api_url": forge.api_base,
        "owner": f.owner,
        "repo": f.repo,
        "base_branch": f.base_branch,
        "secret_name": f.secret_name,
        "landing_policy": f.landing_policy.value,
        "park_timeout_hours": f.park_timeout_hours,
    }


def insert_record(conn: Connection, record: ConfiguredRepository, change: ConfigChange) -> None:
    """The create body, inside the caller's transaction. :class:`RepositorySecretUnavailable`,
    :class:`RepositorySlugTaken` or :class:`RepositoryCoordinateTaken` when refused, and the forge refusals of
    :func:`referred_forge`."""
    forge = referred_forge(conn, record.fields.forge_id, enabled=not record.retired)
    _check_secret(conn, record.fields.secret_name)
    _SLUGS.claim(conn, record.repository_id, record.slug)
    holder = _coordinate_holder(conn, record)
    if holder is not None:
        raise _coordinate_taken(conn, record, holder)
    conn.execute(
        statements_of(conn)
        .insert(repositories)
        .values(
            repository_id=record.repository_id,
            **_columns(record, forge),
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
    forge = referred_forge(conn, record.fields.forge_id, enabled=not record.retired)
    _check_secret(conn, record.fields.secret_name)
    if left_slug is not None:
        _SLUGS.claim(conn, record.repository_id, record.slug)
    holder = _coordinate_holder(conn, record)
    if holder is not None:
        raise _coordinate_taken(conn, record, holder)
    moved = conn.execute(
        statements_of(conn)
        .update(repositories)
        .where(repositories.c.repository_id == record.repository_id, repositories.c.revision == from_revision)
        .values(**_columns(record, forge), revision=record.revision)
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
    """The retire/enable body, inside the caller's transaction. Enabling re-checks the forge, refusing
    :class:`~blizzard.hub.domain.config.forges.ForgeRetired` while it is retired, and the secret."""
    if not retired:
        referred_forge(conn, record.fields.forge_id, enabled=True, enabling=True)
        _check_secret(conn, record.fields.secret_name)
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
        self, record: ConfiguredRepository, *, change: ConfigChange, forge: NewForge | None = None
    ) -> ConfiguredRepository:
        try:
            with self._store.write(
                "create",
                expect=(
                    ConfigFieldError,
                    RepositorySlugTaken,
                    RepositoryCoordinateTaken,
                    IntegrityError,
                    *_FORGE_REFUSALS,
                ),
            ) as conn:
                if forge is not None:
                    insert_new_forge(conn, forge)
                insert_record(conn, record, change)
        except IntegrityError as exc:
            raise self._lost_race(record) from exc
        return record

    def update(
        self,
        record: ConfiguredRepository,
        *,
        from_revision: int,
        left_slug: str | None,
        change: ConfigChange,
        forge: NewForge | None = None,
    ) -> ConfiguredRepository:
        try:
            with self._store.write(
                "update",
                expect=(
                    *_FORGE_REFUSALS,
                    ConfigFieldError,
                    RepositoryCoordinateTaken,
                    RepositorySlugTaken,
                    ConfigRevisionConflict,
                    IntegrityError,
                ),
            ) as conn:
                if forge is not None:
                    insert_new_forge(conn, forge)
                update_record(conn, record, from_revision, left_slug, change)
        except IntegrityError as exc:
            raise self._lost_race(record) from exc
        return record

    def _lost_race(self, record: ConfiguredRepository) -> RepositoryCoordinateTaken | RepositorySlugTaken:
        """The refusal for a concurrent write that took the coordinate or the slug first, decided by a fresh read."""
        with self._store.read("lost_race") as conn:
            holder = _coordinate_holder(conn, record)
            return _coordinate_taken(conn, record, holder) if holder is not None else RepositorySlugTaken(record.slug)

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
        with self._store.write("record_lifecycle", expect=(ConfigFieldError, ConfigRevisionConflict)) as conn:
            record_lifecycle_fact(
                conn, record, retired=retired, from_revision=from_revision, at=at, by=by, change=change
            )
        return record

    def get(self, repository_id: RepositoryId) -> ConfiguredRepository | None:
        return self.get_many([repository_id]).get(repository_id)

    def get_many(self, repository_ids: list[RepositoryId]) -> dict[RepositoryId, ConfiguredRepository]:
        if not repository_ids:
            return {}
        found: dict[RepositoryId, ConfiguredRepository] = {}
        with self._store.read("get_many") as conn:
            retired = _retirements(conn, repository_ids)
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
            retired = _retirements(conn)
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


def _reconstituted(conn: Connection, row: Row[Any]) -> ConfiguredRepository:
    return _of(row, retired_at=_retirements(conn, [RepositoryId(row.repository_id)]).get(row.repository_id))


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
            park_timeout_hours=row.park_timeout_hours,
        ),
        revision=row.revision,
        created_at=row.created_at,
        created_by=row.created_by,
        retired=retired_at is not None,
        retired_at=retired_at,
    )


_repository_store_conforms: type[IWriteRepositoryRecordRepository] = RepositoryRecordStore
