"""SQLAlchemy adapter for the runner hub-token repository seam (package-private).

Host-scoped: ``runner_hub_tokens`` outlives a runner's identity row, so it is a host table, and every statement here
names its runner explicitly. Ciphertext and nonce are stored as base64 ``Text`` (``bzh:sql-portable``)."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, inspect, select
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.logging import get_logger
from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.runner.hub.sealed_tokens import FIRST_REVISION, IWriteHubTokenRepository, SealedHubToken
from blizzard.runner.store.schema import runner_hub_tokens
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class HostHubTokenStore:
    """Read-write adapter over the host's sealed runner hub tokens."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def sealed(self, runner_id: str) -> SealedHubToken | None:
        with self._store.connect() as conn:
            return _held(conn, runner_id)

    def seal_if_absent(self, runner_id: str, sealed: SealedValue, *, at: datetime) -> bool:
        try:
            with self._store.begin() as conn:
                if not seal_if_absent_locked(conn, runner_id, sealed, at=at):
                    return False
        except IntegrityError:
            return False
        _log.info("runner hub token sealed", runner_id=runner_id, revision=FIRST_REVISION, key_id=sealed.key_id)
        return True

    def replace(self, runner_id: str, seal: Callable[[int], SealedValue], *, at: datetime) -> int:
        with self._store.begin() as conn:
            # Take the row's write lock before reading its revision, so two replacements serialize.
            locked = conn.execute(
                runner_hub_tokens.update()
                .where(runner_hub_tokens.c.runner_id == runner_id)
                .values(revision=runner_hub_tokens.c.revision)
            ).rowcount
            if locked:
                revision = int(
                    conn.execute(
                        select(runner_hub_tokens.c.revision).where(runner_hub_tokens.c.runner_id == runner_id)
                    ).scalar_one()
                    + 1
                )
                sealed = seal(revision)
                conn.execute(
                    runner_hub_tokens.update()
                    .where(runner_hub_tokens.c.runner_id == runner_id)
                    .values(revision=revision, updated_at=at, **_sealed_columns(sealed))
                )
            else:
                revision = FIRST_REVISION
                sealed = seal(revision)
                conn.execute(
                    runner_hub_tokens.insert().values(
                        runner_id=runner_id, revision=revision, updated_at=at, **_sealed_columns(sealed)
                    )
                )
        _log.info("runner hub token replaced", runner_id=runner_id, revision=revision, key_id=sealed.key_id)
        return revision

    def drop(self, runner_id: str) -> bool:
        with self._store.begin() as conn:
            if runner_hub_tokens.name not in inspect(conn).get_table_names():
                return False
            dropped = bool(
                conn.execute(runner_hub_tokens.delete().where(runner_hub_tokens.c.runner_id == runner_id)).rowcount
            )
        if dropped:
            _log.info("runner hub token dropped", runner_id=runner_id)
        return dropped


def seal_if_absent_locked(conn: Connection, runner_id: str, sealed: SealedValue, *, at: datetime) -> bool:
    """Insert the runner's row at :data:`FIRST_REVISION` on ``conn``'s open transaction unless one exists; whether it
    was inserted."""
    if _held(conn, runner_id) is not None:
        return False
    conn.execute(
        runner_hub_tokens.insert().values(
            runner_id=runner_id, revision=FIRST_REVISION, updated_at=at, **_sealed_columns(sealed)
        )
    )
    return True


def _held(conn: Connection, runner_id: str) -> SealedHubToken | None:
    row = conn.execute(select(runner_hub_tokens).where(runner_hub_tokens.c.runner_id == runner_id)).first()
    if row is None:
        return None
    return SealedHubToken(
        runner_id=str(row.runner_id),
        revision=int(row.revision),
        sealed=SealedValue(
            key_id=str(row.key_id), ciphertext=base64.b64decode(row.ciphertext), nonce=base64.b64decode(row.nonce)
        ),
        updated_at=row.updated_at,
    )


def _sealed_columns(sealed: SealedValue) -> dict[str, Any]:
    return {
        "key_id": sealed.key_id,
        "ciphertext": base64.b64encode(sealed.ciphertext).decode("ascii"),
        "nonce": base64.b64encode(sealed.nonce).decode("ascii"),
    }


_host_hub_token_store_conforms: type[IWriteHubTokenRepository] = HostHubTokenStore
