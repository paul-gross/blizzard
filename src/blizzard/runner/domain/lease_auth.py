"""Lease-token authorization — the check a worker's attach call must pass first (issue #113).

A plain function over already-loaded values (``bzh:domain-takes-objects``), comparing constant-time
against the same :func:`~blizzard.hub.domain.enrollment.hash_token` digest the mint uses. There is no
``warn``/``enforce`` rollout mode — a token is minted fresh at every spawn and never leaves this
runner, so a mismatch is always a rejection (pinned by tests/test_lease_auth.py)."""

from __future__ import annotations

import hmac
import secrets

from blizzard.hub.domain.enrollment import hash_token

__all__ = ["check_lease_token", "mint_lease_token"]

# The capability token's size — one owner for every mint path (spawn, resume, takeover).
_LEASE_TOKEN_BYTES = 32


def mint_lease_token() -> tuple[str, str]:
    """Mint a lease capability token: ``(plaintext, hash)``. Every mint is a **re-mint** for its lease
    id — overwrite-recorded, invalidating any prior token, since the plaintext is never persisted."""
    token = secrets.token_urlsafe(_LEASE_TOKEN_BYTES)
    return token, hash_token(token)


def check_lease_token(*, presented_token: str | None, stored_hash: str | None) -> bool:
    """``True`` iff ``presented_token`` hashes to ``stored_hash``.

    ``False`` when either side is absent — no token presented, or the lease never
    minted one (a lease pre-dating Phase 1, or an id that resolved to nothing)."""
    if presented_token is None or stored_hash is None:
        return False
    return hmac.compare_digest(hash_token(presented_token), stored_hash)
