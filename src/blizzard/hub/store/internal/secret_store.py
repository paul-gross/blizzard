"""SQLAlchemy adapter for the secret repository seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Ciphertext and
nonce are stored as base64 ``Text`` (``bzh:sql-portable``) and leave this module only
through :meth:`SecretStore.get_sealed`. Retired derives from the append-only
``secret_lifecycle_facts`` table, newest-fact-wins per name."""

from __future__ import annotations

import base64
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.secrets import (
    IResealSecretRepository,
    ISealedSecretRepository,
    IWriteSecretRepository,
    Reseal,
    SealedSecret,
    SealedValue,
    SecretAlreadyExists,
    SecretMetadata,
    SecretRevisionConflict,
    SecretRotationConflict,
    require_unreferenced,
)
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.secret_referrers import active_referrers
from blizzard.hub.store.schema import secret_lifecycle_facts, secrets

_METADATA = (
    secrets.c.name,
    secrets.c.revision,
    secrets.c.replaced_at,
    secrets.c.replaced_by,
    secrets.c.created_at,
)


def secret_unavailable(conn: Connection, name: str) -> bool | None:
    """Why a configured record may not reference ``name``: ``False`` when no such secret
    exists, ``True`` when it is retired, ``None`` when it is usable."""
    if conn.execute(select(secrets.c.name).where(secrets.c.name == name)).first() is None:
        return False
    newest = conn.execute(
        select(secret_lifecycle_facts.c.retired)
        .where(secret_lifecycle_facts.c.name == name)
        .order_by(secret_lifecycle_facts.c.id.desc())
        .limit(1)
    ).first()
    return True if newest is not None and newest.retired else None


def insert_secret(
    conn: Connection, name: str, *, sealed: SealedValue, at: datetime, by: str, change: ConfigChange
) -> None:
    """The create body at revision 1, inside the caller's transaction; a taken name raises :class:`IntegrityError`."""
    conn.execute(
        insert(secrets).values(
            name=name,
            **_sealed_columns(sealed),
            revision=1,
            replaced_at=at,
            replaced_by=by,
            created_at=at,
        )
    )
    append_change(conn, change)


class SecretStore:
    """Read-write secret adapter over the hub store."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def create(self, name: str, *, sealed: SealedValue, at: datetime, by: str, change: ConfigChange) -> SecretMetadata:
        try:
            with self._store.write("create", expect=(IntegrityError,)) as conn:
                insert_secret(conn, name, sealed=sealed, at=at, by=by, change=change)
        except IntegrityError as exc:
            raise SecretAlreadyExists(name) from exc
        return SecretMetadata(name=name, revision=1, replaced_at=at, replaced_by=by, created_at=at)

    def replace(
        self, name: str, *, from_revision: int, sealed: SealedValue, at: datetime, by: str, change: ConfigChange
    ) -> SecretMetadata:
        with self._store.write("replace") as conn:
            moved = conn.execute(
                update(secrets)
                .where(secrets.c.name == name, secrets.c.revision == from_revision)
                .values(**_sealed_columns(sealed), revision=from_revision + 1, replaced_at=at, replaced_by=by)
            ).rowcount
            row = conn.execute(select(*_METADATA).where(secrets.c.name == name)).one()
            if moved == 1:
                append_change(conn, change)
        if moved != 1:
            raise SecretRevisionConflict(name, current=row.revision)
        return self._of(row)

    def get(self, name: str) -> SecretMetadata | None:
        with self._store.read("get") as conn:
            row = conn.execute(select(*_METADATA).where(secrets.c.name == name)).one_or_none()
        return self._of(row) if row is not None else None

    def get_many(self, names: list[str]) -> dict[str, SecretMetadata]:
        if not names:
            return {}
        with self._store.read("get_many") as conn:
            rows = conn.execute(select(*_METADATA).where(secrets.c.name.in_(names))).all()
        return {row.name: self._of(row) for row in rows}

    def list_all(self) -> list[SecretMetadata]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(select(*_METADATA).order_by(secrets.c.name)).all()
        return [self._of(row) for row in rows]

    def is_retired(self, name: str) -> bool:
        with self._store.read("is_retired") as conn:
            row = conn.execute(
                select(secret_lifecycle_facts.c.retired)
                .where(secret_lifecycle_facts.c.name == name)
                .order_by(secret_lifecycle_facts.c.id.desc())
                .limit(1)
            ).first()
        return bool(row.retired) if row is not None else False

    def retired_names(self) -> set[str]:
        with self._store.read("retired_names") as conn:
            rows = conn.execute(
                select(secret_lifecycle_facts.c.name, secret_lifecycle_facts.c.retired).order_by(
                    secret_lifecycle_facts.c.id
                )
            ).all()
        newest: dict[str, bool] = {}
        for row in rows:
            newest[row.name] = row.retired  # ascending id order: the newest fact overwrites
        return {name for name, retired in newest.items() if retired}

    def key_ids_in_use(self) -> set[str]:
        with self._store.read("key_ids_in_use") as conn:
            return set(conn.execute(select(secrets.c.key_id).distinct()).scalars())

    def get_sealed(self, name: str) -> SealedSecret | None:
        with self._store.read("get_sealed") as conn:
            row = conn.execute(
                select(
                    secrets.c.name, secrets.c.revision, secrets.c.key_id, secrets.c.ciphertext, secrets.c.nonce
                ).where(secrets.c.name == name)
            ).one_or_none()
        return self._sealed_of(row) if row is not None else None

    @staticmethod
    def _sealed_of(row) -> SealedSecret:  # type: ignore[no-untyped-def]
        return SealedSecret(
            name=row.name,
            revision=row.revision,
            sealed=SealedValue(
                key_id=row.key_id,
                ciphertext=base64.b64decode(row.ciphertext),
                nonce=base64.b64decode(row.nonce),
            ),
        )

    def list_sealed(self) -> list[SealedSecret]:
        with self._store.read("list_sealed") as conn:
            rows = conn.execute(
                select(
                    secrets.c.name, secrets.c.revision, secrets.c.key_id, secrets.c.ciphertext, secrets.c.nonce
                ).order_by(secrets.c.name)
            ).all()
        return [self._sealed_of(row) for row in rows]

    def reseal(self, changes: list[Reseal]) -> None:
        with self._store.write("reseal", expect=(SecretRotationConflict,)) as conn:
            for change in changes:
                moved = conn.execute(
                    update(secrets)
                    .where(
                        secrets.c.name == change.name,
                        secrets.c.revision == change.revision,
                        secrets.c.key_id == change.from_key_id,
                    )
                    .values(**_sealed_columns(change.sealed))
                ).rowcount
                if moved != 1:
                    raise SecretRotationConflict(change.name)

    def record_lifecycle(self, name: str, *, retired: bool, at: datetime, by: str, change: ConfigChange) -> None:
        with self._store.write("record_lifecycle") as conn:
            if retired:
                require_unreferenced(name, active_referrers(conn, [name])[name])
            conn.execute(insert(secret_lifecycle_facts).values(name=name, retired=retired, set_at=at, set_by=by))
            append_change(conn, change)

    @staticmethod
    def _of(row) -> SecretMetadata:  # type: ignore[no-untyped-def]
        return SecretMetadata(
            name=row.name,
            revision=row.revision,
            replaced_at=row.replaced_at,
            replaced_by=row.replaced_by,
            created_at=row.created_at,
        )


def _sealed_columns(sealed: SealedValue) -> dict[str, str]:
    return {
        "key_id": sealed.key_id,
        "ciphertext": base64.b64encode(sealed.ciphertext).decode("ascii"),
        "nonce": base64.b64encode(sealed.nonce).decode("ascii"),
    }


def _conforms_secret_store(x: SecretStore) -> IWriteSecretRepository:
    return x


def _conforms_sealed_secret_store(x: SecretStore) -> ISealedSecretRepository:
    return x


def _conforms_reseal_secret_store(x: SecretStore) -> IResealSecretRepository:
    return x
