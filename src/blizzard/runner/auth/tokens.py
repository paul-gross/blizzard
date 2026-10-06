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
        Never fabricated: a chunk with no claim here has no token."""
        ...

    def lease_token_hash(self, lease_id: str) -> str | None:
        """The lease's minted capability token hash, or ``None`` if never minted
        here."""
        ...

    def lease_for_token_hash(self, token_hash: str) -> str | None:
        """The lease whose minted token has this hash, or ``None`` — how a request that
        carries only the token, and no lease id, finds the lease it presents."""
        ...


class IWriteTokenRepository(IReadTokenRepository, Protocol):
    """Read-write token store — held only by the domain."""

    def set_route_token(self, chunk_id: str, *, token: str, at: datetime) -> None:
        """Stash a won claim's plaintext route token (upsert).

        A later call for the same chunk overwrites the prior row."""
        ...

    def record_lease_token(self, lease_id: str, token_hash: str, at: datetime) -> None:
        """Persist a lease's capability-token hash.

        Overwrite-safe: a later call for the same lease supersedes the prior hash, invalidating
        the previous token. The plaintext is never persisted, only this sha256 hash."""
        ...
