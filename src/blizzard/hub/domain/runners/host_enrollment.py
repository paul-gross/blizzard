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
    HostLifecycleConflict,
    HostNotEnrolled,
    HostRetired,
    IWriteHostRegistry,
    UnknownRunner,
)
from blizzard.hub.domain.runners.lifecycle_version import HostLifecycleVersion
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
        hold, one under a host, or a retired one refuses the whole add before anything is written, and so does one whose
        lifecycle moved after it was read
        (:class:`~blizzard.hub.domain.runners.registration.RunnerLifecycleConflict`)."""
        adopting = tuple(dict.fromkeys(adopt))
        host_id = Id.mint(IdPrefix.HOST, self._clock).value
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = self._clock.now()
        candidate = Host(host_id=host_id, name=name, added_at=now)
        runners = self._runners_named(adopting)
        for runner in runners:
            candidate.adopt(runner)
        self._hosts.add(
            HostAddition(host_id, name, TokenHash(token).hex, at=now, by=by, adopt=adopting),
            expected_runners=_versions(runners),
        )
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
        (:class:`~blizzard.hub.domain.runners.host.UnknownRunner`), one under a host, and a retired one. The write
        lands only on the host and runners as read: a host retired since raises
        :class:`~blizzard.hub.domain.runners.host.HostRetired`, retirement being its one lifecycle transition, and a
        runner whose lifecycle moved raises
        :class:`~blizzard.hub.domain.runners.registration.RunnerLifecycleConflict`."""
        adopting = tuple(dict.fromkeys(runner_ids))
        runners = self._runners_named(adopting)
        for runner in runners:
            host.adopt(runner)
        try:
            self._hosts.adopt(_lifecycle(host), adopting, expected_runners=_versions(runners))
        except HostLifecycleConflict as exc:
            raise HostRetired(host.host_id, action="adoption") from exc
        _log.info("runners adopted", host_id=host.host_id, runners=list(adopting), by=by)
        return adopting

    def release(self, host: Host, runner_ids: Sequence[str], *, by: str) -> tuple[str, ...]:
        """Return runners under ``host`` to standing alone, keeping their ids and tokens, all or none. Refuses a
        runner that does not stand under ``host``, and an unretired one under a retired host
        (:class:`~blizzard.hub.domain.runners.host.HostRetirementUnfinished`). The write lands only on the host and
        runners as read, raising :class:`~blizzard.hub.domain.runners.host.HostLifecycleConflict` or
        :class:`~blizzard.hub.domain.runners.registration.RunnerLifecycleConflict` otherwise, and
        :class:`~blizzard.hub.domain.runners.host.RunnerNotUnderHost` for a runner that left the host meanwhile."""
        releasing = tuple(dict.fromkeys(runner_ids))
        runners = self._runners_named(releasing)
        for runner in runners:
            host.release(runner)
        self._hosts.release(_lifecycle(host), releasing, expected_runners=_versions(runners))
        _log.info("runners released", host_id=host.host_id, runners=list(releasing), by=by)
        return releasing

    def add_runner(self, host: Host, name: str, *, by: str, projects: Sequence[str] | None = None) -> AddedRunner:
        """Add a runner under ``host`` in its tenant, exactly as an operator's add would, with ``host_id`` set. A
        retired host raises :class:`~blizzard.hub.domain.runners.host.HostRetired`, and so does one retired before
        the add lands."""
        host.refuse_adding_runner()
        try:
            return self._runner_enrollment.add(name, by=by, projects=projects, host=_lifecycle(host))
        except HostLifecycleConflict as exc:
            raise HostRetired(host.host_id, action="adding a runner") from exc

    def _runners_named(self, runner_ids: Sequence[str]) -> list[RunnerRegistration]:
        found = self._runners.get_runners(runner_ids)
        for runner_id in runner_ids:
            if runner_id not in found:
                raise UnknownRunner(runner_id)
        return [found[runner_id] for runner_id in runner_ids]


def _lifecycle(host: Host) -> HostLifecycleVersion:
    """The host's lifecycle version as read, which the write it decided is conditioned on."""
    return HostLifecycleVersion(host.host_id, host.lifecycle_version)


def _versions(runners: Sequence[RunnerRegistration]) -> dict[str, int]:
    """Each runner's lifecycle version as read, which the write it decided is conditioned on."""
    return {runner.runner_id: runner.lifecycle_version for runner in runners}
