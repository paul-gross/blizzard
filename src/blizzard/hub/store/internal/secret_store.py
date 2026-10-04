"""SQLAlchemy adapter for the secret repository seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Ciphertext and
nonce are stored as base64 ``Text`` (``bzh:sql-portable``) and leave this module only
through :meth:`SecretStore.get_sealed`. Retired derives from the append-only
``secret_lifecycle_facts`` table, newest-fact-wins per name."""

from __future__ import annotations

import base64
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from blizzard.hub.domain.secrets import (
    ISealedSecretRepository,
    IWriteSecretRepository,
    SealedSecret,
    SealedValue,
    SecretAlreadyExists,
    SecretRecord,
    SecretRevisionConflict,
)
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import secret_lifecycle_facts, secrets

_METADATA = (
    secrets.c.name,
    secrets.c.revision,
    secrets.c.replaced_at,
    secrets.c.replaced_by,
    secrets.c.created_at,
)


class SecretStore:
    """Read-write secret adapter over the hub store."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def create(self, name: str, *, sealed: SealedValue, at: datetime, by: str) -> SecretRecord:
        try:
            with self._store.write("create", expect=(IntegrityError,)) as conn:
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
        except IntegrityError as exc:
            raise SecretAlreadyExists(name) from exc
        return SecretRecord(name=name, revision=1, replaced_at=at, replaced_by=by, created_at=at)

    def replace(self, name: str, *, from_revision: int, sealed: SealedValue, at: datetime, by: str) -> SecretRecord:
        with self._store.write("replace") as conn:
            moved = conn.execute(
                update(secrets)
                .where(secrets.c.name == name, secrets.c.revision == from_revision)
                .values(**_sealed_columns(sealed), revision=from_revision + 1, replaced_at=at, replaced_by=by)
            ).rowcount
            row = conn.execute(select(*_METADATA).where(secrets.c.name == name)).one()
        if moved != 1:
            raise SecretRevisionConflict(name, current=row.revision)
        return self._of(row)

    def get(self, name: str) -> SecretRecord | None:
        with self._store.read("get") as conn:
            row = conn.execute(select(*_METADATA).where(secrets.c.name == name)).one_or_none()
        return self._of(row) if row is not None else None

    def get_many(self, names: list[str]) -> dict[str, SecretRecord]:
        if not names:
            return {}
        with self._store.read("get_many") as conn:
            rows = conn.execute(select(*_METADATA).where(secrets.c.name.in_(names))).all()
        return {row.name: self._of(row) for row in rows}

    def list_all(self) -> list[SecretRecord]:
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
        if row is None:
            return None
        return SealedSecret(
            name=row.name,
            revision=row.revision,
            sealed=SealedValue(
                key_id=row.key_id,
                ciphertext=base64.b64decode(row.ciphertext),
                nonce=base64.b64decode(row.nonce),
            ),
        )

    def record_lifecycle(self, name: str, *, retired: bool, at: datetime, by: str) -> None:
        with self._store.write("record_lifecycle") as conn:
            conn.execute(insert(secret_lifecycle_facts).values(name=name, retired=retired, set_at=at, set_by=by))

    @staticmethod
    def _of(row) -> SecretRecord:  # type: ignore[no-untyped-def]
        return SecretRecord(
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
