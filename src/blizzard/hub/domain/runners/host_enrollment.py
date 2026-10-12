"""Host enrollment — hub-minted host identities and per-host bearer tokens, and the runners placed under a host.

``add`` mints a host's ``hs_`` id and bearer token together, and ``enroll`` rotates an existing host's token; each
returns the plaintext exactly once. Rotating records the prior hash as revoked in the same write, so the old token is
refused from that instant."""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.domain.runners.enrollment import ROTATION_ACTOR, AddedRunner, RunnerEnrollmentService
from blizzard.hub.domain.runners.host import (
    Host,
    HostAddition,
    HostNotEnrolled,
    IWriteHostRegistry,
    UnknownRunner,
)
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, RunnerRegistration

_log = get_logger("blizzard.hub.host_enrollment")

#: `secrets.token_urlsafe` byte count — 32 bytes -> a 43-character URL-safe token.
_TOKEN_BYTES = 32


@domain_model
@dataclass(frozen=True)
class AddedHost:
    """A host :meth:`HostEnrollmentService.add` just added: its minted id, its name, the plaintext bearer token minted
    with the id — held nowhere else, so returned once — and the runners placed under it."""

    host_id: str
    name: str
    token: str
    adopted: tuple[str, ...] = ()


class HostEnrollmentService:
    """Add hosts, mint or rotate their bearer tokens, and place runners under them; the store keeps only each
    token's sha256 hash."""

    def __init__(
        self,
        *,
        hosts: IWriteHostRegistry,
        runners: IReadRunnerRegistry,
        runner_enrollment: RunnerEnrollmentService,
        clock: IClock,
    ) -> None:
        self._hosts = hosts
        # The tenant's own runners: another tenant's are not found, so they are refused as unknown.
        self._runners = runners
        self._runner_enrollment = runner_enrollment
        self._clock = clock

    def add(self, name: str, *, by: str, adopt: Sequence[str] = ()) -> AddedHost:
        """Mint a host's ``hs_`` id and bearer token together and record it as a never-connected host under ``name``,
        placing the ``adopt`` runners under it in the same write. Names are unique among live hosts: a name a live
        host holds raises :class:`~blizzard.hub.domain.runners.host.HostNameTaken`. A runner the tenant does not
        hold, one under a host, or a retired one refuses the whole add before anything is written."""
        adopting = tuple(dict.fromkeys(adopt))
        host_id = Id.mint(IdPrefix.HOST, self._clock).value
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = self._clock.now()
        candidate = Host(host_id=host_id, name=name, added_at=now)
        for runner in self._runners_named(adopting):
            candidate.adopt(runner)
        self._hosts.add(HostAddition(host_id, name, TokenHash(token).hex, at=now, by=by, adopt=adopting))
        _log.info("host added", host_id=host_id, name=name, by=by, adopted=list(adopting))
        return AddedHost(host_id=host_id, name=name, token=token, adopted=adopting)

    def enroll(self, host: Host) -> str:
        """Mint a fresh token for an added host, connected or not, and return it once. A retired host raises
        :class:`~blizzard.hub.domain.runners.host.HostRetired`."""
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        rotation = host.enroll(TokenHash(token).hex, by=ROTATION_ACTOR, at=self._clock.now())
        revocation_id = self._hosts.rotate_token(rotation)
        _log.info("host token enrolled", host_id=host.host_id, rotated=revocation_id is not None)
        return token

    def revoke_token(self, host: Host, *, by: str) -> int:
        """Revoke the host's current token, issuing none; returns the revocation id. Refuses with
        :class:`~blizzard.hub.domain.runners.host.HostNotEnrolled` when it holds none."""
        revocation = host.revoke_token(by=by, at=self._clock.now())
        revocation_id = self._hosts.revoke_token(revocation.host_id, at=revocation.at, by=revocation.by)
        if revocation_id is None:  # revoked concurrently between the read and the write
            raise HostNotEnrolled(host.host_id)
        _log.info("host token revoked", host_id=host.host_id, by=by)
        return revocation_id

    def adopt(self, host: Host, runner_ids: Sequence[str], *, by: str) -> tuple[str, ...]:
        """Place existing runners under ``host``, all or none. Refuses a runner the tenant does not hold
        (:class:`~blizzard.hub.domain.runners.host.UnknownRunner`), one under a host, and a retired one."""
        adopting = tuple(dict.fromkeys(runner_ids))
        for runner in self._runners_named(adopting):
            host.adopt(runner)
        self._hosts.adopt(host.host_id, adopting)
        _log.info("runners adopted", host_id=host.host_id, runners=list(adopting), by=by)
        return adopting

    def release(self, host: Host, runner_ids: Sequence[str], *, by: str) -> tuple[str, ...]:
        """Return runners under ``host`` to standing alone, keeping their ids and tokens, all or none. Refuses a
        runner that does not stand under ``host``."""
        releasing = tuple(dict.fromkeys(runner_ids))
        for runner in self._runners_named(releasing):
            host.release(runner)
        self._hosts.release(host.host_id, releasing)
        _log.info("runners released", host_id=host.host_id, runners=list(releasing), by=by)
        return releasing

    def add_runner(self, host: Host, name: str, *, by: str, projects: Sequence[str] | None = None) -> AddedRunner:
        """Add a runner under ``host`` in its tenant, exactly as an operator's add would, with ``host_id`` set. A
        retired host raises :class:`~blizzard.hub.domain.runners.host.HostRetired`."""
        host.refuse_adding_runner()
        return self._runner_enrollment.add(name, by=by, projects=projects, host_id=host.host_id)

    def _runners_named(self, runner_ids: Sequence[str]) -> list[RunnerRegistration]:
        runners = []
        for runner_id in runner_ids:
            runner = self._runners.get_runner(runner_id)
            if runner is None:
                raise UnknownRunner(runner_id)
            runners.append(runner)
        return runners
