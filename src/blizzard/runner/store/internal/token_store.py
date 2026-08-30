"""SQLAlchemy adapter for the lease/route-token repository seam (package-private,
blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.logging import get_logger
from blizzard.runner.auth.tokens import IWriteTokenRepository
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import lease_tokens, route_tokens

_log = get_logger("blizzard.runner.store")


class TokenStore:
    """Read-write lease/route-token adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def route_token(self, chunk_id: str) -> str | None:
        rows = self._store.all(select(route_tokens.c.token).where(route_tokens.c.chunk_id == chunk_id))
        return str(rows[0].token) if rows else None

    def lease_token_hash(self, lease_id: str) -> str | None:
        rows = self._store.all(select(lease_tokens.c.token_hash).where(lease_tokens.c.lease_id == lease_id))
        return str(rows[0].token_hash) if rows else None

    def set_route_token(self, chunk_id: str, *, token: str, at: datetime) -> None:
        with self._store.begin() as conn:
            existing = conn.execute(
                select(route_tokens.c.chunk_id).where(route_tokens.c.chunk_id == chunk_id)
            ).one_or_none()
            if existing is None:
                conn.execute(route_tokens.insert().values(chunk_id=chunk_id, token=token, acquired_at=at))
            else:
                conn.execute(
                    route_tokens.update().where(route_tokens.c.chunk_id == chunk_id).values(token=token, acquired_at=at)
                )
        _log.info("route token stashed", chunk_id=chunk_id)

    def record_lease_token(self, lease_id: str, token_hash: str, at: datetime) -> None:
        # Delete-then-insert: a re-mint replaces the prior row under the `lease_id` PK, so
        # the old token is invalidated by construction.
        with self._store.begin() as conn:
            conn.execute(lease_tokens.delete().where(lease_tokens.c.lease_id == lease_id))
            conn.execute(lease_tokens.insert().values(lease_id=lease_id, token_hash=token_hash, minted_at=at))
        _log.info("lease token minted", lease_id=lease_id)


def _conforms_token_store(x: TokenStore) -> IWriteTokenRepository:
    return x
