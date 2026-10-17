"""SQLAlchemy adapter for the secret repository seams (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Ciphertext and
nonce are stored as base64 ``Text`` (``bzh:sql-portable``) and leave this module only
through :meth:`SecretStore.get_sealed`. Retired derives from the append-only
``secret_lifecycle_facts`` table, newest-fact-wins per name."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.batching import id_batches
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
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.internal.secret_referrers import active_referrers
from blizzard.hub.store.schema import secret_lifecycle_facts, secrets
from blizzard.hub.store.scoped import ScopedConnections, statements_of

_METADATA = (
    secrets.c.name,
    secrets.c.revision,
    secrets.c.replaced_at,
    secrets.c.replaced_by,
    secrets.c.created_at,
)


def lock_secret_row(conn: Connection, name: str) -> None:
    """Take the named secret row's write lock, as the first statement after the scoped write's
    tenant-row lock and before any guard read (``bzh:store-exclusive-write``). Secret retire and
    every referrer write take it, so one waits for the other's commit. A missing secret matches
    no row, and the caller's unknown-secret refusal still fires."""
    conn.execute(statements_of(conn).update(secrets).where(secrets.c.name == name).values(name=name))


def secret_unavailable(conn: Connection, name: str) -> bool | None:
    """Why a configured record may not reference ``name``: ``False`` when no such secret
    exists, ``True`` when it is retired, ``None`` when it is usable."""
    if conn.execute(statements_of(conn).select(secrets, secrets.c.name).where(secrets.c.name == name)).first() is None:
        return False
    newest = conn.execute(
        statements_of(conn)
        .select(secret_lifecycle_facts, secret_lifecycle_facts.c.retired)
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
        statements_of(conn)
        .insert(secrets)
        .values(
            name=name,
            **_sealed_columns(sealed),
            revision=1,
            replaced_at=at,
            replaced_by=by,
            created_at=at,
        )
    )
    append_change(conn, change)


def record_lifecycle_fact(
    conn: Connection, name: str, *, retired: bool, at: datetime, by: str, change: ConfigChange
) -> None:
    """The retire/enable body, inside the caller's transaction. Retiring locks the secret row,
    then refuses while an active record references it."""
    if retired:
        lock_secret_row(conn, name)
        require_unreferenced(name, active_referrers(conn, [name])[name])
    conn.execute(
        statements_of(conn).insert(secret_lifecycle_facts).values(name=name, retired=retired, set_at=at, set_by=by)
    )
    append_change(conn, change)


class SecretStore:
    """Read-write secret adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def create(self, name: str, *, sealed: SealedValue, at: datetime, by: str, change: ConfigChange) -> SecretMetadata:
        try:
            with self._store.write("create", expect=(IntegrityError,)) as conn:
                insert_secret(conn, name, sealed=sealed, at=at, by=by, change=change)
        except IntegrityError as exc:
            raise SecretAlreadyExists(name) from exc
        return SecretMetadata(name=name, revision=1, replaced_at=at, replaced_by=by, created_at=at, retired=False)

    def replace(
        self, name: str, *, from_revision: int, sealed: SealedValue, at: datetime, by: str, change: ConfigChange
    ) -> SecretMetadata:
        with self._store.write("replace") as conn:
            moved = conn.execute(
                self._store.update(secrets)
                .where(secrets.c.name == name, secrets.c.revision == from_revision)
                .values(**_sealed_columns(sealed), revision=from_revision + 1, replaced_at=at, replaced_by=by)
            ).rowcount
            row = conn.execute(self._store.select(secrets, *_METADATA).where(secrets.c.name == name)).one()
            if moved == 1:
                append_change(conn, change)
            retired = name in self._retired_among(conn, [name])
        if moved != 1:
            raise SecretRevisionConflict(name, current=row.revision)
        return self._of(row, retired=retired)

    def get(self, name: str) -> SecretMetadata | None:
        with self._store.read("get") as conn:
            row = conn.execute(self._store.select(secrets, *_METADATA).where(secrets.c.name == name)).one_or_none()
            retired = row is not None and name in self._retired_among(conn, [name])
        return self._of(row, retired=retired) if row is not None else None

    def get_many(self, names: list[str]) -> dict[str, SecretMetadata]:
        if not names:
            return {}
        found: dict[str, SecretMetadata] = {}
        with self._store.read("get_many") as conn:
            for batch in id_batches(names):
                rows = conn.execute(self._store.select(secrets, *_METADATA).where(secrets.c.name.in_(batch))).all()
                retired = self._retired_among(conn, batch)
                found.update({row.name: self._of(row, retired=row.name in retired) for row in rows})
        return found

    def list_all(self) -> list[SecretMetadata]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(self._store.select(secrets, *_METADATA).order_by(secrets.c.name)).all()
            retired = self._retired_among(conn, None)
        return [self._of(row, retired=row.name in retired) for row in rows]

    def is_retired(self, name: str) -> bool:
        return name in self.retired_names([name])

    def retired_names(self, names: Sequence[str] | None = None) -> set[str]:
        if names is not None and not names:
            return set()
        with self._store.read("retired_names") as conn:
            return self._retired_among(conn, names)

    def _retired_among(self, conn, names: Sequence[str] | None) -> set[str]:  # type: ignore[no-untyped-def]
        """Every name of ``names`` (all when ``None``) whose newest lifecycle fact reads retired —
        the one derivation :meth:`retired_names` and every metadata load share."""
        facts = secret_lifecycle_facts
        found: set[str] = set()
        for batch in id_batches(list(dict.fromkeys(names))) if names is not None else [None]:
            found.update(conn.execute(newest_retired_select(self._store, facts, facts.c.name, batch)).scalars())
        return found

    def key_ids_in_use(self) -> set[str]:
        with self._store.read("key_ids_in_use") as conn:
            return set(conn.execute(self._store.select(secrets, secrets.c.key_id).distinct()).scalars())

    def get_sealed(self, name: str) -> SealedSecret | None:
        with self._store.read("get_sealed") as conn:
            row = conn.execute(
                self._store.select(
                    secrets, secrets.c.name, secrets.c.revision, secrets.c.key_id, secrets.c.ciphertext, secrets.c.nonce
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
                self._store.select(
                    secrets, secrets.c.name, secrets.c.revision, secrets.c.key_id, secrets.c.ciphertext, secrets.c.nonce
                ).order_by(secrets.c.name)
            ).all()
        return [self._sealed_of(row) for row in rows]

    def reseal(self, changes: list[Reseal]) -> None:
        with self._store.write("reseal", expect=(SecretRotationConflict,)) as conn:
            for change in changes:
                moved = conn.execute(
                    self._store.update(secrets)
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
            record_lifecycle_fact(conn, name, retired=retired, at=at, by=by, change=change)

    @staticmethod
    def _of(row, *, retired: bool) -> SecretMetadata:  # type: ignore[no-untyped-def]
        return SecretMetadata(
            name=row.name,
            revision=row.revision,
            replaced_at=row.replaced_at,
            replaced_by=row.replaced_by,
            created_at=row.created_at,
            retired=retired,
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
