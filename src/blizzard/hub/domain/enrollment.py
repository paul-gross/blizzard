"""Runner enrollment — hub-minted per-runner bearer tokens (issue #86a).

An operator enrolls a runner into an identity the hub trusts: ``enroll`` mints
``secrets.token_urlsafe(32)``, hashes it (sha256 hex), persists only the hash via the
write registry, and returns the plaintext — the caller (the enroll endpoint) prints or
returns it exactly once and keeps no other copy. Re-enrolling an already-enrolled
runner rotates: the new hash overwrites the old one in place (the registration row is
a mutable upsert, not an append-only fact — see ``hub/domain/registry.py``'s module
docstring), so the prior token stops resolving via ``registration_for_token_hash``
immediately. There is no separate revoke: rotating *is* revoking the old token.

Deliberately not folded into :class:`~blizzard.hub.domain.registry.FleetService`:
enrollment is an operator act on a runner's *identity*, not a fleet registration event
(no ``last_seen_at``/``paused`` brake is touched here), so it gets its own service
holding just the write registry and the injected clock (``bzh:injected-clock``).
"""

from __future__ import annotations

import hashlib
import secrets

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.registry import IWriteRunnerRegistry, RunnerRegistration

_log = get_logger("blizzard.hub.enrollment")

#: `secrets.token_urlsafe` byte count — 32 bytes -> a 43-character URL-safe token,
#: comfortably beyond brute-force range for a bearer credential.
_TOKEN_BYTES = 32


def hash_token(token: str) -> str:
    """The sha256 hex digest a presented bearer token is compared against.

    The single hashing function both the mint path (here) and the resolve path
    (``hub/api/auth.py``'s ``require_runner_principal``) call, so the two can never
    drift onto different digests of the same token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RunnerEnrollmentService:
    """Mint or rotate a runner's bearer token; the store keeps only its sha256 hash."""

    def __init__(self, *, registry: IWriteRunnerRegistry, clock: IClock) -> None:
        self._registry = registry
        self._clock = clock

    def enroll(self, runner: RunnerRegistration) -> str:
        """Mint a fresh token for an already-registered runner and return it once.

        Takes the loaded :class:`~blizzard.hub.domain.registry.RunnerRegistration`
        rather than a bare id (``bzh:domain-takes-objects``) — the enroll endpoint
        resolves ``runner_id`` to its row (404 if unknown) before calling this."""
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        self._registry.set_token_hash(runner.runner_id, token_hash=hash_token(token), at=self._clock.now())
        _log.info("runner token enrolled", runner_id=runner.runner_id)
        return token
