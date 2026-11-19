"""Each runner's hub bearer token, sealed in the host store under the host's key.

A token is sealed under the name ``runner-hub-token/<runner id>`` at its row's revision, so the seal binds both: a row
moved to another runner, or replayed at another revision, refuses to open. The prefix keeps a runner's hub-token binding
apart from any hub secret's under a shared key. The rules and ports live here; the adapter is
``src/blizzard/runner/store/internal/host_hub_token_store.py``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.foundation.secrets.sealing import ISecretCipher, SealedSecret, SealedValue, SecretUnreadable
from blizzard.foundation.secrets.value import SecretValue

SEALED_NAME_PREFIX = "runner-hub-token/"

#: The revision a runner's first sealed token takes.
FIRST_REVISION = 1


def sealed_name(runner_id: str) -> str:
    """The name a runner's token is sealed under — bound into the seal with the row's revision."""
    return f"{SEALED_NAME_PREFIX}{runner_id}"


def seal_token(
    cipher: ISecretCipher, runner_id: str, token: SecretValue, *, revision: int = FIRST_REVISION
) -> SealedValue:
    """``token`` sealed as the runner's row at ``revision``."""
    return cipher.seal(token, name=sealed_name(runner_id), revision=revision)


class ITokenVerdict(Protocol):
    """A runner's held identity verdict, as far as replacing its token reads it."""

    def token_retired(self) -> bool:
        """Whether the hub last refused the runner's token as retired."""
        ...


def token_set_command(runner_name: str) -> str:
    """The verb that seals a runner's token again, as an operator types it."""
    return f"`blizzard runner token set --runner {runner_name}`"


def unreadable_token(key_id: str | None) -> str:
    """Why a runner refuses whose row does not open: the key generation it names."""
    return f"its hub token in the host store does not open under the host's key (generation {key_id})"


def refuse_retired(runner_id: str, verdict: ITokenVerdict | None) -> None:
    """Refuse replacing the token of a runner whose held ``verdict`` is its hub's refusal of it as retired
    (:class:`HubTokenRetired`); a runner holding no verdict is not refused."""
    if verdict is not None and verdict.token_retired():
        raise HubTokenRetired(runner_id)


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

    def stored(self, cipher: ISecretCipher | None) -> StoredToken:
        """The row opened under ``cipher``; unreadable when it does not open, or with no cipher, a host holding no
        key."""
        if cipher is None:
            return StoredToken.unreadable(self.sealed.key_id, self.revision)
        try:
            value = cipher.open(self.secret())
        except SecretUnreadable as exc:
            return StoredToken.unreadable(exc.key_id, self.revision)
        return StoredToken.opened(value.expose(), self.revision)


class StoredTokenState(StrEnum):
    """What a runner's token row holds for the process that reads it."""

    #: The row opens under the host's key.
    OPENED = "opened"
    #: The row exists but does not open under the host's key: the runner refuses until its token is set again.
    UNREADABLE = "unreadable"
    #: The runner holds no row.
    ABSENT = "absent"


@domain_model
@dataclass(frozen=True)
class StoredToken:
    """A runner's stored-token outcome: the opened value and its revision, the key generation a row that does not
    open names, or no row at all."""

    state: StoredTokenState
    value: str = field(default="", repr=False)
    revision: int | None = None
    key_id: str | None = None

    @classmethod
    def opened(cls, value: str, revision: int) -> StoredToken:
        return cls(StoredTokenState.OPENED, value, revision)

    @classmethod
    def unreadable(cls, key_id: str, revision: int) -> StoredToken:
        return cls(StoredTokenState.UNREADABLE, revision=revision, key_id=key_id)

    @classmethod
    def absent(cls) -> StoredToken:
        return cls(StoredTokenState.ABSENT)

    @classmethod
    def of(cls, row: SealedHubToken | None, cipher: ISecretCipher | None) -> StoredToken:
        """The outcome of reading ``row`` under ``cipher``; absent with no row."""
        return cls.absent() if row is None else row.stored(cipher)


class IReadHubTokenRepository(Protocol):
    def sealed(self, runner_id: str) -> SealedHubToken | None: ...

    def key_ids(self) -> frozenset[str]:
        """The key generations every runner's row is sealed under; empty for a store behind the revision that adds
        the table."""
        ...


class IWriteHubTokenRepository(IReadHubTokenRepository, Protocol):
    def seal_if_absent(self, runner_id: str, sealed: SealedValue, *, at: datetime) -> bool:
        """Insert the runner's row at :data:`FIRST_REVISION` unless one exists; whether it was inserted. An existing
        row is never overwritten, whether or not it opens."""
        ...

    def replace(self, runner_id: str, seal: Callable[[int], SealedValue], *, at: datetime) -> int:
        """In one transaction, seal at the row's next revision — :data:`FIRST_REVISION` for a runner with no row —
        and write it; the revision written. ``seal`` is local work only, called under the transaction."""
        ...

    def drop(self, runner_id: str) -> bool:
        """Delete the runner's row, for a runner leaving this host's store; whether one went. A store behind the
        revision that adds the table holds none."""
        ...


class HubTokens:
    """Seals, opens and replaces a runner's stored token."""

    def __init__(self, repository: IWriteHubTokenRepository, cipher: ISecretCipher, clock: IClock) -> None:
        self._repository = repository
        self._cipher = cipher
        self._clock = clock

    def stored(self, runner_id: str) -> StoredToken:
        """The runner's stored-token outcome; never raises for a row that does not open."""
        return StoredToken.of(self._repository.sealed(runner_id), self._cipher)

    def revision(self, runner_id: str) -> int | None:
        """The revision the runner's row is at, or ``None`` when it holds none — one primary-key read."""
        row = self._repository.sealed(runner_id)
        return None if row is None else row.revision

    def seal_first(self, runner_id: str, token: str) -> SealedValue:
        """``token`` sealed as the runner's first row, for a writer that inserts it on its own transaction."""
        return seal_token(self._cipher, runner_id, SecretValue.entered(token))

    def seal_if_absent(self, runner_id: str, token: SecretValue) -> bool:
        """Seal ``token`` as the runner's first row, unless it holds one; whether it was sealed."""
        sealed = seal_token(self._cipher, runner_id, token)
        return self._repository.seal_if_absent(runner_id, sealed, at=self._clock.now())

    def replace(self, runner_id: str, raw: str, *, verdict: ITokenVerdict | None) -> int:
        """Seal ``raw`` at the runner's next revision; the revision written. :class:`SecretValueBlank` for a blank
        value, :class:`HubTokenRetired` when the runner's held ``verdict`` refuses it (:func:`refuse_retired`)."""
        token = SecretValue.entered(raw)
        refuse_retired(runner_id, verdict)

        def seal(revision: int) -> SealedValue:
            return seal_token(self._cipher, runner_id, token, revision=revision)

        return self._repository.replace(runner_id, seal, at=self._clock.now())


class RunnerHubTokenSource:
    """One runner's row as the held token reads it at each identity check."""

    def __init__(self, tokens: HubTokens, runner_id: str) -> None:
        self._tokens = tokens
        self._runner_id = runner_id

    def revision(self) -> int | None:
        return self._tokens.revision(self._runner_id)

    def stored(self) -> StoredToken:
        return self._tokens.stored(self._runner_id)
