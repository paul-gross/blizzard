"""The lease- and route-token repository seam.

Two independent capability tokens: a chunk's route claim token and a
lease's attach capability token hash. Neither plaintext is
persisted except the route token itself, which the runner alone ever presents."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

__all__ = ["IReadTokenRepository", "IWriteTokenRepository"]


class IReadTokenRepository(Protocol):
    """Read-only token queries (held by read-path edges)."""

    def route_token(self, chunk_id: str) -> str | None:
        """The chunk's stashed route capability token, or ``None`` if never claimed here.
        Stamped onto every chunk-scoped outbound payload at enqueue.
        ``None`` is presented as an absent field, never fabricated."""
        ...

    def lease_token_hash(self, lease_id: str) -> str | None:
        """The lease's minted capability token hash, or ``None`` if never minted
        here — what an attach authorization check compares a
        presented plaintext's hash against."""
        ...

    def lease_for_token_hash(self, token_hash: str) -> str | None:
        """The lease whose minted token has this hash, or ``None`` — how a request that
        carries only the token, and no lease id, finds the lease it presents."""
        ...


class IWriteTokenRepository(IReadTokenRepository, Protocol):
    """Read-write token store — held only by the domain."""

    def set_route_token(self, chunk_id: str, *, token: str, at: datetime) -> None:
        """Stash a won claim's plaintext route token (upsert).

        Called on a won claim with the token the claim response returned once. A fresh
        claim overwrites a prior row for the same chunk."""
        ...

    def record_lease_token(self, lease_id: str, token_hash: str, at: datetime) -> None:
        """Persist a lease's capability-token hash.

        Overwrite-safe: the implementation replaces any prior row, invalidating the
        previous token. The plaintext is never persisted, only this sha256 hash."""
        ...
