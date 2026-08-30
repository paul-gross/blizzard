"""The lease- and route-token repository seam (blizzard#410).

Two independent capability tokens: a chunk's route claim token (issue #84a) and a
lease's attach capability token hash (issue #113, Phase 1). Neither plaintext is
persisted except the route token itself, which the runner alone ever presents."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

__all__ = ["IReadTokenRepository", "IWriteTokenRepository"]


class IReadTokenRepository(Protocol):
    """Read-only token queries (held by read-path edges)."""

    def route_token(self, chunk_id: str) -> str | None:
        """The chunk's stashed route capability token, or ``None`` if never claimed here
        (issue #84a). Stamped onto every chunk-scoped outbound payload at enqueue.
        ``None`` is presented as an absent field, never fabricated."""
        ...

    def lease_token_hash(self, lease_id: str) -> str | None:
        """The lease's minted capability token hash, or ``None`` if never minted
        here (issue #113, Phase 1) — what an attach authorization check compares a
        presented plaintext's hash against."""
        ...


class IWriteTokenRepository(IReadTokenRepository, Protocol):
    """Read-write token store — held only by the domain."""

    def set_route_token(self, chunk_id: str, *, token: str, at: datetime) -> None:
        """Stash a won claim's plaintext route token (upsert) — issue #84a.

        Called on a won claim with the token the claim response returned once. A fresh
        claim overwrites a prior row for the same chunk."""
        ...

    def record_lease_token(self, lease_id: str, token_hash: str, at: datetime) -> None:
        """Persist a lease's capability-token hash (issue #113, Phase 1).

        Overwrite-safe: the implementation replaces any prior row, invalidating the
        previous token. The plaintext is never persisted, only this sha256 hash."""
        ...
