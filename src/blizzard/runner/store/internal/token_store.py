"""SQLAlchemy adapter for the lease/route-token repository seam (package-private)."""

from __future__ import annotations

from datetime import datetime

from blizzard.foundation.logging import get_logger
from blizzard.runner.auth.tokens import IWriteTokenRepository
from blizzard.runner.store.schema import lease_tokens, route_tokens
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class TokenStore:
    """Read-write lease/route-token adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def route_token(self, chunk_id: str) -> str | None:
        rows = self._store.all(
            self._store.select(route_tokens, route_tokens.c.token).where(route_tokens.c.chunk_id == chunk_id)
        )
        return str(rows[0].token) if rows else None

    def lease_token_hash(self, lease_id: str) -> str | None:
        rows = self._store.all(
            self._store.select(lease_tokens, lease_tokens.c.token_hash).where(lease_tokens.c.lease_id == lease_id)
        )
        return str(rows[0].token_hash) if rows else None

    def lease_for_token_hash(self, token_hash: str) -> str | None:
        rows = self._store.all(
            self._store.select(lease_tokens, lease_tokens.c.lease_id).where(lease_tokens.c.token_hash == token_hash)
        )
        return str(rows[0].lease_id) if rows else None

    def set_route_token(self, chunk_id: str, *, token: str, at: datetime) -> None:
        st = self._store
        with st.begin() as conn:
            existing = conn.execute(
                st.select(route_tokens, route_tokens.c.chunk_id).where(route_tokens.c.chunk_id == chunk_id)
            ).one_or_none()
            if existing is None:
                conn.execute(st.insert(route_tokens).values(chunk_id=chunk_id, token=token, acquired_at=at))
            else:
                conn.execute(
                    st.update(route_tokens)
                    .where(route_tokens.c.chunk_id == chunk_id)
                    .values(token=token, acquired_at=at)
                )
        _log.info("route token stashed", chunk_id=chunk_id)

    def record_lease_token(self, lease_id: str, token_hash: str, at: datetime) -> None:
        # Delete-then-insert: a re-mint replaces the prior row under the `lease_id` PK, so
        # the old token is invalidated by construction.
        st = self._store
        with st.begin() as conn:
            conn.execute(st.delete(lease_tokens).where(lease_tokens.c.lease_id == lease_id))
            conn.execute(st.insert(lease_tokens).values(lease_id=lease_id, token_hash=token_hash, minted_at=at))
        _log.info("lease token minted", lease_id=lease_id)


_token_store_conforms: type[IWriteTokenRepository] = TokenStore
