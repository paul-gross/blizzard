"""The sealing seam — key generations, sealed values, and the cipher, key-provider, and repository protocols.

A value is sealed under a key generation by an :class:`ISecretCipher`; the generations are held outside the
store by an :class:`IKeyProvider` (``bzh:pluggable-seams``)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.secrets.value import SecretValue


class SecretUnreadable(Exception):
    """A sealed value failed to open: its key generation is absent or it was tampered
    with, moved to another row, or replayed at another revision."""

    def __init__(self, name: str, *, key_id: str) -> None:
        super().__init__(f"secret {name} cannot be opened under key generation {key_id}")
        self.name = name
        self.key_id = key_id


class SecretRotationConflict(Exception):
    """A row changed between a rotation's read and its commit — nothing was re-sealed."""

    def __init__(self, name: str) -> None:
        super().__init__(f"secret {name} changed during key rotation; nothing was re-sealed — re-run it")
        self.name = name


@domain_model
@dataclass(frozen=True)
class KeyGeneration:
    """One key generation: its fingerprint id and its 32 bytes of key material."""

    key_id: str
    material: bytes = field(repr=False)


@domain_model
@dataclass(frozen=True)
class SealedValue:
    """A value sealed under one key generation for one ``(name, revision)``."""

    key_id: str
    ciphertext: bytes
    nonce: bytes


@domain_model
@dataclass(frozen=True)
class SealedSecret:
    name: str
    revision: int
    sealed: SealedValue


@domain_model
@dataclass(frozen=True)
class Reseal:
    """One row's value re-sealed under another key generation at the same revision."""

    name: str
    revision: int
    from_key_id: str
    sealed: SealedValue


def uncovered_key_ids(in_use: Collection[str], available: Collection[str]) -> frozenset[str]:
    """Every key generation a stored row is sealed under that no available generation answers."""
    return frozenset(in_use) - frozenset(available)


def stale_seals(rows: Sequence[SealedSecret], target_key_id: str) -> list[SealedSecret]:
    """The rows a rotation into ``target_key_id`` re-seals: every row sealed under another generation."""
    return [row for row in rows if row.sealed.key_id != target_key_id]


class ISealedSecretRepository(Protocol):
    """Read-only access to a secret's sealed row — its ciphertext and nonce."""

    def get_sealed(self, name: str) -> SealedSecret | None: ...


class IResealSecretRepository(ISealedSecretRepository, Protocol):
    """Adds whole-store re-sealing under another key generation."""

    def list_sealed(self) -> list[SealedSecret]: ...

    def reseal(self, changes: list[Reseal]) -> None:
        """Apply every change in one transaction, each a compare-and-set on
        ``(revision, key_id)``; :class:`SecretRotationConflict` on any miss, with nothing
        applied. Moves only ``key_id``, ``ciphertext``, and ``nonce``."""
        ...


class IKeyProvider(Protocol):
    """The key's generations, held outside the store (``bzh:pluggable-seams``)."""

    def current(self) -> KeyGeneration: ...

    def generation(self, key_id: str) -> KeyGeneration | None: ...

    def available_ids(self) -> frozenset[str]: ...


class IRotatableKeyProvider(IKeyProvider, Protocol):
    """A key source an offline rotation can move to a new current generation."""

    def mint(self) -> KeyGeneration:
        """The generation rotation seals into: a freshly minted one, not yet current — or the current one, when the
        source's generations are fixed outside the process, whose :meth:`promote` and :meth:`prune` then change
        nothing."""
        ...

    def promote(self, key_id: str) -> None:
        """Make ``key_id`` the current generation."""
        ...

    def prune(self, referenced: frozenset[str]) -> None:
        """Drop every generation no stored row references."""
        ...


class ISecretCipher(Protocol):
    """Authenticated sealing bound to ``(name, revision)``."""

    def seal(self, value: SecretValue, *, name: str, revision: int) -> SealedValue: ...

    def open(self, secret: SealedSecret) -> SecretValue:
        """Raises :class:`SecretUnreadable` on a missing generation or failed tag."""
        ...
