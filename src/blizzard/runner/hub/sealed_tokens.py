"""Each runner's hub bearer token, sealed in the host store under the host's key.

A token is sealed under the name ``runner-hub-token/<runner id>`` at its row's revision, so the seal binds both: a row
moved to another runner, or replayed at another revision, refuses to open. The prefix keeps a host token's binding
apart from any hub secret's under a shared key. The rules and ports live here; the adapter is
``src/blizzard/runner/store/internal/host_hub_token_store.py``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.foundation.secrets.sealing import ISecretCipher, SealedSecret, SealedValue
from blizzard.foundation.secrets.value import SecretValue

SEALED_NAME_PREFIX = "runner-hub-token/"

#: The revision a runner's first sealed token takes.
FIRST_REVISION = 1


def sealed_name(runner_id: str) -> str:
    """The name a runner's token is sealed under — bound into the seal with the row's revision."""
    return f"{SEALED_NAME_PREFIX}{runner_id}"


class HubTokenRetired(Exception):
    """The hub refused the runner's token as retired: a retired runner's token is never replaced."""

    def __init__(self, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} is retired at its hub; its token is not replaced")
        self.runner_id = runner_id


@domain_model
@dataclass(frozen=True)
class SealedHubToken:
    """One runner's stored token row: its seal, the revision the seal binds, and when it was last written."""

    runner_id: str
    revision: int
    sealed: SealedValue
    updated_at: datetime

    def secret(self) -> SealedSecret:
        return SealedSecret(sealed_name(self.runner_id), self.revision, self.sealed)


@domain_model
@dataclass(frozen=True)
class OpenedHubToken:
    """A runner's token opened from its row, with the revision it was sealed at."""

    value: SecretValue
    revision: int


class IReadHubTokenRepository(Protocol):
    def sealed(self, runner_id: str) -> SealedHubToken | None: ...


class IWriteHubTokenRepository(IReadHubTokenRepository, Protocol):
    def seal_if_absent(self, runner_id: str, sealed: SealedValue, *, at: datetime) -> bool:
        """Insert the runner's row at :data:`FIRST_REVISION` unless one exists; whether it was inserted. An existing
        row is never overwritten, whether or not it opens."""
        ...

    def replace(self, runner_id: str, seal: Callable[[int], SealedValue], *, at: datetime) -> int:
        """In one transaction, seal at the row's next revision — :data:`FIRST_REVISION` for a runner with no row —
        and write it; the revision written. ``seal`` is local work only, called under the transaction."""
        ...


class HubTokens:
    """Seals, opens and replaces a runner's stored token."""

    def __init__(self, repository: IWriteHubTokenRepository, cipher: ISecretCipher, clock: IClock) -> None:
        self._repository = repository
        self._cipher = cipher
        self._clock = clock

    def open(self, runner_id: str) -> OpenedHubToken | None:
        """The runner's token, or ``None`` when it holds no row; :class:`SecretUnreadable` when its row does not
        open under the host's key."""
        row = self._repository.sealed(runner_id)
        if row is None:
            return None
        return OpenedHubToken(self._cipher.open(row.secret()), row.revision)

    def seal_if_absent(self, runner_id: str, token: SecretValue) -> bool:
        """Seal ``token`` as the runner's first row, unless it holds one; whether it was sealed."""
        sealed = self._cipher.seal(token, name=sealed_name(runner_id), revision=FIRST_REVISION)
        return self._repository.seal_if_absent(runner_id, sealed, at=self._clock.now())

    def replace(self, runner_id: str, raw: str, *, retired: bool) -> int:
        """Seal ``raw`` at the runner's next revision; the revision written. :class:`SecretValueBlank` for a blank
        value, :class:`HubTokenRetired` for a runner its hub refused as retired."""
        token = SecretValue.entered(raw)
        if retired:
            raise HubTokenRetired(runner_id)

        def seal(revision: int) -> SealedValue:
            return self._cipher.seal(token, name=sealed_name(runner_id), revision=revision)

        return self._repository.replace(runner_id, seal, at=self._clock.now())
