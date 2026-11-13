"""SQLAlchemy adapter for the work-source record seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Retired derives
from the append-only ``work_source_lifecycle_facts`` table, newest-fact-wins per id
(``bzh:facts-not-status``). Every write commits its change row in the same transaction.

Every write runs in the caller's unit, which declares the keyed locks of the forge and the secret the source names;
whether it may name them is the domain's judgment, not this adapter's."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    IWriteWorkSourceRepository,
    WorkSourceFields,
    WorkSourceId,
    WorkSourceLocatorTaken,
    WorkSourceSlug,
    WorkSourceSlugTaken,
    resolve_work_source_reference,
)
from blizzard.hub.domain.kernel.transactions import LockedTransaction

# Appends the config audit record on the caller's connection, atomically with the write.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.schema import work_source_former_slugs, work_source_lifecycle_facts, work_sources
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_for

_SLUGS = SlugTables(work_sources, work_source_former_slugs, "work_source_id", WorkSourceSlugTaken)


def retired_ids(conn: Connection, ids: list[WorkSourceId] | None = None) -> set[str]:
    facts = work_source_lifecycle_facts
    found: set[str] = set()
    for batch in id_batches(ids) if ids is not None else [None]:
        found.update(
            conn.execute(newest_retired_select(statements_of(conn), facts, facts.c.work_source_id, batch)).scalars()
        )
    return found


def _locator_holder(conn: Connection, record: ConfiguredWorkSource) -> str | None:
    row = conn.execute(
        statements_of(conn)
        .select(work_sources, work_sources.c.slug)
        .where(
            work_sources.c.provider == record.fields.provider,
            work_sources.c.forge_id == record.fields.forge_id,
            work_sources.c.locator == record.fields.locator,
            work_sources.c.work_source_id != record.work_source_id,
        )
    ).first()
    return None if row is None else row.slug


def _columns(record: ConfiguredWorkSource) -> dict[str, object]:
    f = record.fields
    return {
        "slug": record.slug,
        "name": record.name,
        "provider": f.provider,
        "locator": f.locator,
        "forge_id": f.forge_id,
        "annotate": f.annotate,
        "secret_name": f.secret,
    }


def insert_record(conn: Connection, record: ConfiguredWorkSource, change: ConfigChange) -> None:
    """The create body, inside the caller's transaction. :class:`WorkSourceSlugTaken` or
    :class:`WorkSourceLocatorTaken` when refused."""
    _SLUGS.claim(conn, record.work_source_id, record.slug)
    holder = _locator_holder(conn, record)
    if holder is not None:
        raise WorkSourceLocatorTaken(record.fields.provider, record.fields.locator, holder=holder)
    conn.execute(
        statements_of(conn)
        .insert(work_sources)
        .values(
            work_source_id=record.work_source_id,
            **_columns(record),
            revision=record.revision,
            created_at=record.created_at,
            created_by=record.created_by,
        )
    )
    append_change(conn, change)


def update_record(
    conn: Connection, record: ConfiguredWorkSource, from_revision: int, left_slug: str | None, change: ConfigChange
) -> None:
    """The compare-and-set edit body, inside the caller's transaction, moving the slug when ``left_slug`` is set."""
    if left_slug is not None:
        _SLUGS.claim(conn, record.work_source_id, record.slug)
    holder = _locator_holder(conn, record)
    if holder is not None:
        raise WorkSourceLocatorTaken(record.fields.provider, record.fields.locator, holder=holder)
    moved = conn.execute(
        statements_of(conn)
        .update(work_sources)
        .where(work_sources.c.work_source_id == record.work_source_id, work_sources.c.revision == from_revision)
        .values(**_columns(record), revision=record.revision)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict(
            "work source", record.work_source_id, current=_revision(conn, record.work_source_id)
        )
    if left_slug is not None:
        _SLUGS.release(conn, record.work_source_id, left_slug, left_at=change.recorded_at)
    append_change(conn, change)


def record_lifecycle_fact(
    conn: Connection,
    record: ConfiguredWorkSource,
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
        .update(work_sources)
        .where(work_sources.c.work_source_id == record.work_source_id, work_sources.c.revision == from_revision)
        .values(revision=record.revision)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict(
            "work source", record.work_source_id, current=_revision(conn, record.work_source_id)
        )
    conn.execute(
        statements_of(conn)
        .insert(work_source_lifecycle_facts)
        .values(work_source_id=record.work_source_id, retired=retired, set_at=at, set_by=by)
    )
    append_change(conn, change)


def _lost_race(conn: Connection, record: ConfiguredWorkSource) -> WorkSourceLocatorTaken | WorkSourceSlugTaken:
    """The refusal a write that lost the locator or the slug to a concurrent one reads as, on ``conn``."""
    holder = _locator_holder(conn, record)
    if holder is not None:
        return WorkSourceLocatorTaken(record.fields.provider, record.fields.locator, holder=holder)
    return WorkSourceSlugTaken(record.slug)


def _revision(conn: Connection, work_source_id: str) -> int:
    return conn.execute(
        statements_of(conn)
        .select(work_sources, work_sources.c.revision)
        .where(work_sources.c.work_source_id == work_source_id)
    ).scalar_one()


class WorkSourceRecordStore:
    """Read-write work-source adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def create(
        self, record: ConfiguredWorkSource, *, change: ConfigChange, transaction: LockedTransaction
    ) -> ConfiguredWorkSource:
        conn = self._connection(transaction, record)
        try:
            with conn.begin_nested():
                insert_record(conn, record, change)
        except IntegrityError as exc:
            raise _lost_race(conn, record) from exc
        return record

    def update(
        self,
        record: ConfiguredWorkSource,
        *,
        from_revision: int,
        left_slug: str | None,
        change: ConfigChange,
        transaction: LockedTransaction,
    ) -> ConfiguredWorkSource:
        conn = self._connection(transaction, record)
        try:
            with conn.begin_nested():
                update_record(conn, record, from_revision, left_slug, change)
        except IntegrityError as exc:
            raise _lost_race(conn, record) from exc
        return record

    def record_lifecycle(
        self,
        record: ConfiguredWorkSource,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
        transaction: LockedTransaction,
    ) -> ConfiguredWorkSource:
        conn = self._connection(transaction, record)
        record_lifecycle_fact(conn, record, retired=retired, from_revision=from_revision, at=at, by=by, change=change)
        return record

    def _connection(self, transaction: LockedTransaction, record: ConfiguredWorkSource) -> Connection:
        """``transaction``'s connection, which must lock every key ``record`` names."""
        for key in record.locks():
            connection_for(self._store, transaction, key=key)
        return connection_for(self._store, transaction)

    def get(self, work_source_id: WorkSourceId) -> ConfiguredWorkSource | None:
        return self.get_many([work_source_id]).get(work_source_id)

    def get_many(self, work_source_ids: list[WorkSourceId]) -> dict[WorkSourceId, ConfiguredWorkSource]:
        if not work_source_ids:
            return {}
        found: dict[WorkSourceId, ConfiguredWorkSource] = {}
        with self._store.read("get_many") as conn:
            retired = retired_ids(conn, work_source_ids)
            for batch in id_batches(work_source_ids):
                rows = conn.execute(
                    self._store.select(work_sources).where(work_sources.c.work_source_id.in_(batch))
                ).all()
                found.update(
                    {WorkSourceId(row.work_source_id): _of(row, retired=row.work_source_id in retired) for row in rows}
                )
        return found

    def list_all(self, *, include_retired: bool) -> list[ConfiguredWorkSource]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(self._store.select(work_sources).order_by(work_sources.c.slug)).all()
            retired = retired_ids(conn)
        return [
            _of(row, retired=row.work_source_id in retired)
            for row in rows
            if include_retired or row.work_source_id not in retired
        ]

    def get_by_slug(self, slug: WorkSourceSlug) -> ConfiguredWorkSource | None:
        with self._store.read("get_by_slug") as conn:
            row = conn.execute(self._store.select(work_sources).where(work_sources.c.slug == slug.value)).first()
            return None if row is None else _reconstituted(conn, row)

    def get_by_former_slug(self, slug: WorkSourceSlug) -> ConfiguredWorkSource | None:
        left = self._store.scope_join(
            work_source_former_slugs,
            work_sources,
            work_source_former_slugs.c.work_source_id == work_sources.c.work_source_id,
        )
        with self._store.read("get_by_former_slug") as conn:
            row = conn.execute(
                self._store.select(left, *work_sources.c).where(work_source_former_slugs.c.slug == slug.value)
            ).first()
            return None if row is None else _reconstituted(conn, row)

    def resolve(self, value: str) -> ConfiguredWorkSource | None:
        return resolve_work_source_reference(value, self)


def _reconstituted(conn: Connection, row: Row[Any]) -> ConfiguredWorkSource:
    return _of(row, retired=row.work_source_id in retired_ids(conn, [WorkSourceId(row.work_source_id)]))


def _of(row: Row[Any], *, retired: bool) -> ConfiguredWorkSource:
    return ConfiguredWorkSource(
        work_source_id=WorkSourceId(row.work_source_id),
        slug=row.slug,
        name=row.name,
        fields=WorkSourceFields(
            provider=row.provider,
            locator=row.locator,
            forge_id=ForgeId(row.forge_id),
            annotate=row.annotate,
            secret=row.secret_name,
        ),
        revision=row.revision,
        created_at=row.created_at,
        created_by=row.created_by,
        retired=retired,
    )


_work_source_store_conforms: type[IWriteWorkSourceRepository] = WorkSourceRecordStore
