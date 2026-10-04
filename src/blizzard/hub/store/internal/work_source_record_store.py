"""SQLAlchemy adapter for the work-source record seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Retired derives
from the append-only ``work_source_lifecycle_facts`` table, newest-fact-wins per name
(``bzh:facts-not-status``). Every write commits its change row in the same transaction."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.work_sources import (
    ConfigRevisionConflict,
    IWriteWorkSourceRepository,
    WorkSourceFields,
    WorkSourceLocatorTaken,
    WorkSourceNameTaken,
    WorkSourceRecord,
    WorkSourceSecretUnavailable,
)
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.secret_referrers import retired_names_query
from blizzard.hub.store.internal.secret_store import secret_unavailable
from blizzard.hub.store.schema import work_source_lifecycle_facts, work_sources


def _retired_names_query():  # type: ignore[no-untyped-def]
    return retired_names_query(work_source_lifecycle_facts)


def _check_secret(conn: Connection, secret: str | None) -> None:
    if secret is None:
        return
    retired = secret_unavailable(conn, secret)
    if retired is not None:
        raise WorkSourceSecretUnavailable(secret, retired=retired)


def _columns(record: WorkSourceRecord) -> dict[str, object]:
    f = record.fields
    return {
        "provider": f.provider,
        "locator": f.locator,
        "api_base": f.api_base,
        "web_base": f.web_base,
        "annotate": f.annotate,
        "secret_name": f.secret,
    }


class WorkSourceRecordStore:
    """Read-write work-source adapter over the hub store."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def create(self, record: WorkSourceRecord, *, change: ConfigChange) -> WorkSourceRecord:
        try:
            with self._store.write(
                "create", expect=(WorkSourceSecretUnavailable, WorkSourceNameTaken, WorkSourceLocatorTaken)
            ) as conn:
                _check_secret(conn, record.fields.secret)
                if conn.execute(select(work_sources.c.name).where(work_sources.c.name == record.name)).first():
                    raise WorkSourceNameTaken(record.name)
                holder = conn.execute(
                    select(work_sources.c.name).where(
                        work_sources.c.provider == record.fields.provider,
                        work_sources.c.locator == record.fields.locator,
                    )
                ).first()
                if holder is not None:
                    raise WorkSourceLocatorTaken(record.fields.provider, record.fields.locator, holder=holder.name)
                conn.execute(
                    insert(work_sources).values(
                        name=record.name,
                        **_columns(record),
                        revision=record.revision,
                        created_at=record.created_at,
                        created_by=record.created_by,
                    )
                )
                append_change(conn, change)
        except IntegrityError as exc:  # a concurrent create won the race
            raise WorkSourceNameTaken(record.name) from exc
        return record

    def update(self, record: WorkSourceRecord, *, from_revision: int, change: ConfigChange) -> WorkSourceRecord:
        with self._store.write(
            "update", expect=(WorkSourceSecretUnavailable, WorkSourceLocatorTaken, ConfigRevisionConflict)
        ) as conn:
            _check_secret(conn, record.fields.secret)
            holder = conn.execute(
                select(work_sources.c.name).where(
                    work_sources.c.provider == record.fields.provider,
                    work_sources.c.locator == record.fields.locator,
                    work_sources.c.name != record.name,
                )
            ).first()
            if holder is not None:
                raise WorkSourceLocatorTaken(record.fields.provider, record.fields.locator, holder=holder.name)
            moved = conn.execute(
                update(work_sources)
                .where(work_sources.c.name == record.name, work_sources.c.revision == from_revision)
                .values(**_columns(record), revision=record.revision)
            ).rowcount
            if moved != 1:
                raise ConfigRevisionConflict("work source", record.name, current=self._revision(conn, record.name))
            append_change(conn, change)
        return record

    def record_lifecycle(
        self,
        record: WorkSourceRecord,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
    ) -> WorkSourceRecord:
        with self._store.write(
            "record_lifecycle", expect=(WorkSourceSecretUnavailable, ConfigRevisionConflict)
        ) as conn:
            if not retired:
                _check_secret(conn, record.fields.secret)
            moved = conn.execute(
                update(work_sources)
                .where(work_sources.c.name == record.name, work_sources.c.revision == from_revision)
                .values(revision=record.revision)
            ).rowcount
            if moved != 1:
                raise ConfigRevisionConflict("work source", record.name, current=self._revision(conn, record.name))
            conn.execute(
                insert(work_source_lifecycle_facts).values(name=record.name, retired=retired, set_at=at, set_by=by)
            )
            append_change(conn, change)
        return record

    @staticmethod
    def _revision(conn: Connection, name: str) -> int:
        return conn.execute(select(work_sources.c.revision).where(work_sources.c.name == name)).scalar_one()

    def get(self, name: str) -> WorkSourceRecord | None:
        return self.get_many([name]).get(name)

    def get_many(self, names: list[str]) -> dict[str, WorkSourceRecord]:
        if not names:
            return {}
        with self._store.read("get_many") as conn:
            rows = conn.execute(select(work_sources).where(work_sources.c.name.in_(names))).all()
            retired = set(
                conn.execute(_retired_names_query().where(work_source_lifecycle_facts.c.name.in_(names))).scalars()
            )
        return {row.name: self._of(row, retired=row.name in retired) for row in rows}

    def list_all(self, *, include_retired: bool) -> list[WorkSourceRecord]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(select(work_sources).order_by(work_sources.c.name)).all()
            retired = set(conn.execute(_retired_names_query()).scalars())
        return [
            self._of(row, retired=row.name in retired) for row in rows if include_retired or row.name not in retired
        ]

    @staticmethod
    def _of(row, *, retired: bool) -> WorkSourceRecord:  # type: ignore[no-untyped-def]
        return WorkSourceRecord(
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
