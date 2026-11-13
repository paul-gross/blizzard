"""SQLAlchemy adapter for the credential record seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Retired derives from the append-only
``credential_lifecycle_facts`` table, newest-fact-wins per id (``bzh:facts-not-status``). Every write commits its change
row in the same transaction, and ``settings`` is stored as the binding's canonical JSON object.

Every write runs in the caller's unit, which declares the keyed locks of the forge and the secret the credential names
— and an edit, a retire and an enable the credential's own. Whether a credential may name them, or be retired, is the
domain's judgment; this adapter refuses only a slug or an authority another credential holds
(``bzh:store-refuses-stale``)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.engine import Connection, Row
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.credential_naming import CredentialId, credential_lock
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialAuthorityTaken,
    CredentialFields,
    CredentialSlug,
    CredentialSlugTaken,
    IWriteCredentialRepository,
    resolve_credential_reference,
)
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction

# Appends the config audit record on the caller's connection, atomically with the write.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.schema import credential_former_slugs, credential_lifecycle_facts, credentials
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_for, connection_in

_SLUGS = SlugTables(credentials, credential_former_slugs, "credential_id", CredentialSlugTaken)


def _retired_ids(conn: Connection, ids: list[CredentialId] | None = None) -> set[str]:
    facts = credential_lifecycle_facts
    found: set[str] = set()
    for batch in id_batches(ids) if ids is not None else [None]:
        found.update(
            conn.execute(newest_retired_select(statements_of(conn), facts, facts.c.credential_id, batch)).scalars()
        )
    return found


def _authority_holder(conn: Connection, record: ConfiguredCredential) -> str | None:
    row = conn.execute(
        statements_of(conn)
        .select(credentials, credentials.c.slug)
        .where(
            credentials.c.forge_id == record.fields.forge_id,
            credentials.c.authority == record.authority,
            credentials.c.credential_id != record.credential_id,
        )
    ).first()
    return None if row is None else row.slug


def _columns(record: ConfiguredCredential) -> dict[str, object]:
    f = record.fields
    return {
        "slug": f.slug,
        "name": f.name,
        "secret_name": f.secret_name,
        "settings": json.dumps(dict(f.settings), sort_keys=True),
        "authority": record.authority,
    }


def _refuse_held_authority(conn: Connection, record: ConfiguredCredential) -> None:
    holder = _authority_holder(conn, record)
    if holder is not None:
        raise CredentialAuthorityTaken(record.authority, holder=holder)


def insert_credential(conn: Connection, record: ConfiguredCredential, change: ConfigChange) -> None:
    """The create body, inside the caller's transaction. :class:`CredentialSlugTaken` or
    :class:`CredentialAuthorityTaken` when refused."""
    _SLUGS.claim(conn, record.credential_id, record.slug)
    _refuse_held_authority(conn, record)
    conn.execute(
        statements_of(conn)
        .insert(credentials)
        .values(
            credential_id=record.credential_id,
            forge_id=record.fields.forge_id,
            binding=record.fields.binding,
            **_columns(record),
            revision=record.revision,
            created_at=record.created_at,
            created_by=record.created_by,
        )
    )
    append_change(conn, change)


def update_credential(
    conn: Connection, record: ConfiguredCredential, from_revision: int, left_slug: str | None, change: ConfigChange
) -> None:
    """The compare-and-set edit body, inside the caller's transaction, moving the slug when ``left_slug`` is set."""
    if left_slug is not None:
        _SLUGS.claim(conn, record.credential_id, record.slug)
    _refuse_held_authority(conn, record)
    moved = conn.execute(
        statements_of(conn)
        .update(credentials)
        .where(credentials.c.credential_id == record.credential_id, credentials.c.revision == from_revision)
        .values(**_columns(record), revision=record.revision)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("credential", record.credential_id, current=_revision(conn, record.credential_id))
    if left_slug is not None:
        _SLUGS.release(conn, record.credential_id, left_slug, left_at=change.recorded_at)
    append_change(conn, change)


def record_credential_lifecycle(
    conn: Connection,
    record: ConfiguredCredential,
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
        .update(credentials)
        .where(credentials.c.credential_id == record.credential_id, credentials.c.revision == from_revision)
        .values(revision=record.revision)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("credential", record.credential_id, current=_revision(conn, record.credential_id))
    conn.execute(
        statements_of(conn)
        .insert(credential_lifecycle_facts)
        .values(credential_id=record.credential_id, retired=retired, set_at=at, set_by=by)
    )
    append_change(conn, change)


def _revision(conn: Connection, credential_id: str) -> int:
    return conn.execute(
        statements_of(conn)
        .select(credentials, credentials.c.revision)
        .where(credentials.c.credential_id == credential_id)
    ).scalar_one()


def _lost_race(conn: Connection, record: ConfiguredCredential) -> CredentialAuthorityTaken | CredentialSlugTaken:
    """The refusal a write that lost the authority or the slug to a concurrent one reads as, on ``conn``."""
    holder = _authority_holder(conn, record)
    return CredentialAuthorityTaken(record.authority, holder=holder) if holder else CredentialSlugTaken(record.slug)


class CredentialRecordStore:
    """Read-write credential adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def create(
        self, record: ConfiguredCredential, *, change: ConfigChange, transaction: LockedTransaction
    ) -> ConfiguredCredential:
        conn = self._connection(transaction, record)
        try:
            with conn.begin_nested():
                insert_credential(conn, record, change)
        except IntegrityError as exc:
            raise _lost_race(conn, record) from exc
        return record

    def update(
        self,
        record: ConfiguredCredential,
        *,
        from_revision: int,
        left_slug: str | None,
        change: ConfigChange,
        transaction: LockedTransaction,
    ) -> ConfiguredCredential:
        conn = self._connection(transaction, record, own=True)
        try:
            with conn.begin_nested():
                update_credential(conn, record, from_revision, left_slug, change)
        except IntegrityError as exc:
            raise _lost_race(conn, record) from exc
        return record

    def record_lifecycle(
        self,
        record: ConfiguredCredential,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
        transaction: LockedTransaction,
    ) -> ConfiguredCredential:
        conn = connection_for(self._store, transaction, key=credential_lock(record.credential_id))
        record_credential_lifecycle(
            conn, record, retired=retired, from_revision=from_revision, at=at, by=by, change=change
        )
        return record

    def _connection(
        self, transaction: LockedTransaction, record: ConfiguredCredential, *, own: bool = False
    ) -> Connection:
        """``transaction``'s connection, which must lock every key ``record`` names — and, when ``own``, the
        credential's."""
        keys = [*record.locks(), *([credential_lock(record.credential_id)] if own else [])]
        for key in keys:
            connection_for(self._store, transaction, key=key)
        return connection_for(self._store, transaction)

    def get(self, credential_id: CredentialId) -> ConfiguredCredential | None:
        return self.get_many([credential_id]).get(credential_id)

    def get_many(
        self, credential_ids: list[CredentialId], *, transaction: Transaction | None = None
    ) -> dict[CredentialId, ConfiguredCredential]:
        if not credential_ids:
            return {}
        found: dict[CredentialId, ConfiguredCredential] = {}
        with connection_in(self._store, transaction, "get_many") as conn:
            retired = _retired_ids(conn, credential_ids)
            for batch in id_batches(credential_ids):
                rows = conn.execute(self._store.select(credentials).where(credentials.c.credential_id.in_(batch))).all()
                found.update(
                    {CredentialId(row.credential_id): _of(row, retired=row.credential_id in retired) for row in rows}
                )
        return found

    def list_all(self, *, include_retired: bool) -> list[ConfiguredCredential]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(self._store.select(credentials).order_by(credentials.c.slug)).all()
            retired = _retired_ids(conn)
        return [
            _of(row, retired=row.credential_id in retired)
            for row in rows
            if include_retired or row.credential_id not in retired
        ]

    def get_by_slug(self, slug: CredentialSlug) -> ConfiguredCredential | None:
        with self._store.read("get_by_slug") as conn:
            row = conn.execute(self._store.select(credentials).where(credentials.c.slug == slug.value)).first()
            return None if row is None else _reconstituted(conn, row)

    def get_by_former_slug(self, slug: CredentialSlug) -> ConfiguredCredential | None:
        left = self._store.scope_join(
            credential_former_slugs,
            credentials,
            credential_former_slugs.c.credential_id == credentials.c.credential_id,
        )
        with self._store.read("get_by_former_slug") as conn:
            row = conn.execute(
                self._store.select(left, *credentials.c).where(credential_former_slugs.c.slug == slug.value)
            ).first()
            return None if row is None else _reconstituted(conn, row)

    def resolve(self, value: str) -> ConfiguredCredential | None:
        return resolve_credential_reference(value, self)

    def get_by_authority(
        self, forge_id: ForgeId, authority: str, *, transaction: Transaction | None = None
    ) -> ConfiguredCredential | None:
        with connection_in(self._store, transaction, "get_by_authority") as conn:
            row = conn.execute(
                statements_of(conn)
                .select(credentials)
                .where(credentials.c.forge_id == forge_id, credentials.c.authority == authority)
            ).first()
            return None if row is None else _reconstituted(conn, row)

    def held_slugs(self, *, transaction: Transaction | None = None) -> frozenset[str]:
        with connection_in(self._store, transaction, "held_slugs") as conn:
            stmts = statements_of(conn)
            current = conn.execute(stmts.select(credentials, credentials.c.slug)).scalars()
            former = conn.execute(stmts.select(credential_former_slugs, credential_former_slugs.c.slug)).scalars()
            return frozenset([*current, *former])


def _reconstituted(conn: Connection, row: Row[Any]) -> ConfiguredCredential:
    return _of(row, retired=row.credential_id in _retired_ids(conn, [CredentialId(row.credential_id)]))


def _of(row: Row[Any], *, retired: bool) -> ConfiguredCredential:
    return ConfiguredCredential(
        credential_id=CredentialId(row.credential_id),
        fields=CredentialFields(
            slug=row.slug,
            name=row.name,
            forge_id=ForgeId(row.forge_id),
            binding=row.binding,
            secret_name=row.secret_name,
            settings=json.loads(row.settings),
        ),
        authority=row.authority,
        revision=row.revision,
        created_at=row.created_at,
        created_by=row.created_by,
        retired=retired,
    )


_credential_store_conforms: type[IWriteCredentialRepository] = CredentialRecordStore
