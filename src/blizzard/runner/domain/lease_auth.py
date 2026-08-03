"""Lease-token authorization — the check a worker's attach call must pass before its
own semantics run (issue #113, Phase 2).

Mirrors ``hub/domain/route_auth.py``'s ``check_route_token`` shape: a plain function
taking already-loaded values (``bzh:domain-takes-objects``), not a service — the
caller resolves the lease's stored hash via
:meth:`~blizzard.runner.store.repository.IReadRunnerStore.lease_token_hash` and
passes it in, so this stays a pure function with no store dependency of its own.
Comparison is constant-time (``hmac.compare_digest``) against the sha256 hex digest —
the same :func:`~blizzard.hub.domain.enrollment.hash_token` the mint
(``runner/loop/steps.py``'s ``_spawn_attempt``) uses, so mint and check can never
drift onto different digests of the same secret.

Unlike ``check_route_token``, there is no ``warn``/``enforce`` rollout mode here: a
lease token is minted fresh at every spawn and never leaves this runner, so there is
no existing-deployment population to roll out against — a mismatch is always a
rejection.
"""

from __future__ import annotations

import hmac
import secrets

from blizzard.hub.domain.enrollment import hash_token

__all__ = ["check_lease_token", "mint_lease_token"]

# The capability token's size — one owner for every mint path (spawn, resume, takeover).
_LEASE_TOKEN_BYTES = 32


def mint_lease_token() -> tuple[str, str]:
    """Mint a lease capability token: ``(plaintext, hash)``.

    The one mint, paired with :func:`check_lease_token`'s digest so the two can never
    drift (the module docstring's contract). Pure — the caller records the hash via
    ``record_lease_token`` and carries the plaintext to the child env; every mint is a
    **re-mint** for its lease id (overwrite-recorded), invalidating any prior token,
    since the plaintext is never persisted.
    """
    token = secrets.token_urlsafe(_LEASE_TOKEN_BYTES)
    return token, hash_token(token)


def check_lease_token(*, presented_token: str | None, stored_hash: str | None) -> bool:
    """``True`` iff ``presented_token`` hashes to ``stored_hash``.

    ``False`` when either side is absent — no token presented, or the lease never
    minted one (a lease pre-dating Phase 1, or an id that resolved to nothing)."""
    if presented_token is None or stored_hash is None:
        return False
    return hmac.compare_digest(hash_token(presented_token), stored_hash)
