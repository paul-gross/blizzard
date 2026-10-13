"""SQLAlchemy adapter for the work-source record seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Retired derives
from the append-only ``work_source_lifecycle_facts`` table, newest-fact-wins per id
(``bzh:facts-not-status``). Every write commits its change row in the same transaction."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.work_sources import (
    ConfigRevisionConflict,
    ConfiguredWorkSource,
    IWriteWorkSourceRepository,
    WorkSourceFields,
    WorkSourceId,
    WorkSourceLocatorTaken,
    WorkSourceSecretUnavailable,
    WorkSourceSlug,
    WorkSourceSlugTaken,
    resolve_work_source_reference,
)
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.internal.secret_store import secret_unavailable
from blizzard.hub.store.schema import work_source_former_slugs, work_source_lifecycle_facts, work_sources
from blizzard.hub.store.scoped import ScopedConnections, statements_of

_SLUGS = SlugTables(work_sources, work_source_former_slugs, "work_source_id", WorkSourceSlugTaken)


def _retired_ids(conn: Connection, ids: list[WorkSourceId] | None = None) -> set[str]:
    facts = work_source_lifecycle_facts
    found: set[str] = set()
    for batch in id_batches(ids) if ids is not None else [None]:
        found.update(
            conn.execute(newest_retired_select(statements_of(conn), facts, facts.c.work_source_id, batch)).scalars()
        )
    return found


def _check_secret(conn: Connection, secret: str | None) -> None:
    if secret is None:
        return
    retired = secret_unavailable(conn, secret)
    if retired is not None:
        raise WorkSourceSecretUnavailable(secret, retired=retired)


def _locator_holder(conn: Connection, record: ConfiguredWorkSource) -> str | None:
    row = conn.execute(
        statements_of(conn)
        .select(work_sources, work_sources.c.slug)
        .where(
            work_sources.c.provider == record.fields.provider,
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
        "api_base": f.api_base,
        "web_base": f.web_base,
        "annotate": f.annotate,
        "secret_name": f.secret,
    }


def insert_record(conn: Connection, record: ConfiguredWorkSource, change: ConfigChange) -> None:
    """The create body, inside the caller's transaction. :class:`WorkSourceSecretUnavailable`,
    :class:`WorkSourceSlugTaken` or :class:`WorkSourceLocatorTaken` when refused."""
    _check_secret(conn, record.fields.secret)
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
    _check_secret(conn, record.fields.secret)
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
    """The retire/enable body, inside the caller's transaction. Enabling re-checks the secret."""
    if not retired:
        _check_secret(conn, record.fields.secret)
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

    def create(self, record: ConfiguredWorkSource, *, change: ConfigChange) -> ConfiguredWorkSource:
        try:
            with self._store.write(
                "create", expect=(WorkSourceSecretUnavailable, WorkSourceSlugTaken, WorkSourceLocatorTaken)
            ) as conn:
                insert_record(conn, record, change)
        except IntegrityError as exc:  # a concurrent create won the race
            raise WorkSourceSlugTaken(record.slug) from exc
        return record

    def update(
        self, record: ConfiguredWorkSource, *, from_revision: int, left_slug: str | None, change: ConfigChange
    ) -> ConfiguredWorkSource:
        try:
            with self._store.write(
                "update",
                expect=(
                    WorkSourceSecretUnavailable,
                    WorkSourceLocatorTaken,
                    WorkSourceSlugTaken,
                    ConfigRevisionConflict,
                ),
            ) as conn:
                update_record(conn, record, from_revision, left_slug, change)
        except IntegrityError as exc:  # a concurrent write took the slug first
            raise WorkSourceSlugTaken(record.slug) from exc
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
    ) -> ConfiguredWorkSource:
        with self._store.write(
            "record_lifecycle", expect=(WorkSourceSecretUnavailable, ConfigRevisionConflict)
        ) as conn:
            record_lifecycle_fact(
                conn, record, retired=retired, from_revision=from_revision, at=at, by=by, change=change
            )
        return record

    def get(self, work_source_id: WorkSourceId) -> ConfiguredWorkSource | None:
        return self.get_many([work_source_id]).get(work_source_id)

    def get_many(self, work_source_ids: list[WorkSourceId]) -> dict[WorkSourceId, ConfiguredWorkSource]:
        if not work_source_ids:
            return {}
        found: dict[WorkSourceId, ConfiguredWorkSource] = {}
        with self._store.read("get_many") as conn:
            retired = _retired_ids(conn, work_source_ids)
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
            retired = _retired_ids(conn)
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


def _reconstituted(conn: Connection, row: Row) -> ConfiguredWorkSource:  # type: ignore[type-arg]
    return _of(row, retired=row.work_source_id in _retired_ids(conn, [WorkSourceId(row.work_source_id)]))


def _of(row: Row, *, retired: bool) -> ConfiguredWorkSource:  # type: ignore[type-arg]
    return ConfiguredWorkSource(
        work_source_id=WorkSourceId(row.work_source_id),
        slug=row.slug,
        name=row.name,
        fields=WorkSourceFields(
            provider=row.provider,
            locator=row.locator,
            api_base=row.api_base,
            web_base=row.web_base,
            annotate=row.annotate,
            secret=row.secret_name,
        ),
        revision=row.revision,
        created_at=row.created_at,
        created_by=row.created_by,
        retired=retired,
    )


def _conforms_work_source_store(x: WorkSourceRecordStore) -> IWriteWorkSourceRepository:
    return x
