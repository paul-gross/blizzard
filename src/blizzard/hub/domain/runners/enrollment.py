"""Runner enrollment — hub-minted per-runner bearer tokens.

``enroll`` mints a token, persists only its sha256 hex hash, and returns the plaintext
exactly once. Re-enrolling rotates: the prior hash is recorded as revoked in the same write,
so the old token is refused under every runner-auth mode from that instant.
"""

from __future__ import annotations

import secrets

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry, RunnerRegistration

_log = get_logger("blizzard.hub.enrollment")

#: `secrets.token_urlsafe` byte count — 32 bytes -> a 43-character URL-safe token,
#: comfortably beyond brute-force range for a bearer credential.
_TOKEN_BYTES = 32

#: Who a rotation's revocation is recorded as — enrollment is an operator verb with no actor field.
ROTATION_ACTOR = "operator"


class RunnerEnrollmentService:
    """Mint or rotate a runner's bearer token; the store keeps only its sha256 hash."""

    def __init__(self, *, registry: IWriteRunnerRegistry, clock: IClock) -> None:
        self._registry = registry
        self._clock = clock

    def enroll(self, runner: RunnerRegistration) -> str:
        """Mint a fresh token for an already-registered runner and return it once.

        Takes the loaded :class:`~blizzard.hub.domain.runners.registration.RunnerRegistration`
        rather than a bare id (``bzh:domain-takes-objects``) — the enroll endpoint
        resolves ``runner_id`` to its row (404 if unknown); a retired runner raises ``RunnerRetired``."""
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        rotation = runner.enroll(TokenHash(token).hex, by=ROTATION_ACTOR, at=self._clock.now())
        revocation_id = self._registry.rotate_token(rotation)
        _log.info("runner token enrolled", runner_id=runner.runner_id, rotated=revocation_id is not None)
        return token
