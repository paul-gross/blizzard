"""Runner enrollment — hub-minted runner identities and per-runner bearer tokens.

``add`` mints a runner's ``rn_`` id and bearer token together, and ``enroll`` rotates an existing
runner's token; each returns the plaintext exactly once. Rotating records the prior hash as revoked
in the same write, so the old token is refused from that instant."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry, RunnerAddition, RunnerRegistration

_log = get_logger("blizzard.hub.enrollment")

#: `secrets.token_urlsafe` byte count — 32 bytes -> a 43-character URL-safe token,
#: comfortably beyond brute-force range for a bearer credential.
_TOKEN_BYTES = 32

#: Who a rotation's revocation is recorded as — enrollment is an operator verb with no actor field.
ROTATION_ACTOR = "operator"


@domain_model
@dataclass(frozen=True)
class AddedRunner:
    """A runner :meth:`RunnerEnrollmentService.add` just added: its minted id, its initial name,
    and the plaintext bearer token minted with the id — held nowhere else, so returned once."""

    runner_id: str
    name: str
    token: str


class RunnerEnrollmentService:
    """Add runners and mint or rotate their bearer tokens; the store keeps only each token's sha256 hash."""

    def __init__(self, *, registry: IWriteRunnerRegistry, clock: IClock) -> None:
        self._registry = registry
        self._clock = clock

    def add(self, name: str, *, by: str) -> AddedRunner:
        """Mint a runner's ``rn_`` id and bearer token together and record it as a never-connected
        registration under the initial ``name``, added now by ``by``. Names are not unique: adding a
        name some runner already holds adds another runner."""
        runner_id = Id.mint(IdPrefix.RUNNER, self._clock).value
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        self._registry.add(RunnerAddition(runner_id, name, TokenHash(token).hex, at=self._clock.now(), by=by))
        _log.info("runner added", runner_id=runner_id, name=name, by=by)
        return AddedRunner(runner_id=runner_id, name=name, token=token)

    def enroll(self, runner: RunnerRegistration) -> str:
        """Mint a fresh token for an added runner, connected or not, and return it once.

        Takes the loaded :class:`~blizzard.hub.domain.runners.registration.RunnerRegistration`
        rather than a bare id (``bzh:domain-takes-objects``) — the enroll endpoint
        resolves ``runner_id`` to its row (404 if unknown); a retired runner raises ``RunnerRetired``."""
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        rotation = runner.enroll(TokenHash(token).hex, by=ROTATION_ACTOR, at=self._clock.now())
        revocation_id = self._registry.rotate_token(rotation)
        _log.info("runner token enrolled", runner_id=runner.runner_id, rotated=revocation_id is not None)
        return token
