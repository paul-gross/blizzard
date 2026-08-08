"""Lease-token authorization — the check a worker's attach call must pass first (issue #113).

:class:`LeaseToken` owns the mint and the constant-time check against its
:class:`~blizzard.hub.domain.enrollment.TokenHash` digest. There is no ``warn``/``enforce`` rollout
mode — a token is minted fresh at every spawn and never leaves this runner."""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass

from blizzard.hub.domain.enrollment import TokenHash

__all__ = ["LeaseToken"]

# The capability token's size — one owner for every mint path (spawn, resume, takeover).
_LEASE_TOKEN_BYTES = 32


@dataclass(frozen=True)
class LeaseToken:
    """A presented lease capability token, against the digest its lease recorded."""

    presented: str | None
    stored_hash: str | None

    @classmethod
    def mint(cls) -> tuple[str, str]:
        """A fresh token as ``(plaintext, hash)``. Every mint is a **re-mint** for its lease id —
        overwrite-recorded, invalidating any prior token, since the plaintext is never persisted."""
        token = secrets.token_urlsafe(_LEASE_TOKEN_BYTES)
        return token, TokenHash(token).hex

    @property
    def valid(self) -> bool:
        """``False`` when either side is absent — no token presented, or the lease never
        minted one (a lease pre-dating Phase 1, or an id that resolved to nothing)."""
        if self.presented is None or self.stored_hash is None:
            return False
        return hmac.compare_digest(TokenHash(self.presented).hex, self.stored_hash)
