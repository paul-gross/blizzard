"""Secret domain model — a named credential written once, sealed at rest, never returned.

A value enters through ``ConfigAuthoring``, is sealed under the hub key by an
:class:`ISecretCipher`, and leaves only through :class:`ISecretReader` as a
:class:`SecretValue` whose ``repr``/``str`` are redacted (``bzh:secret-write-only``).
Writes go through ``ConfigAuthoring``. Retire/enable is a newest-fact-wins brake,
a scope's shape (``bzh:facts-not-status``)."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import ConfigChange, RecordRef

_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")
_REDACTED = "SecretValue(<redacted>)"


class SecretNameError(ValueError):
    """A secret name is empty or outside ``[a-z0-9-]+`` — names the offending value."""


class SecretNotFound(LookupError):
    def __init__(self, name: str) -> None:
        super().__init__(f"unknown secret {name}")
        self.name = name


class SecretAlreadyExists(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"secret {name} already exists")
        self.name = name


class SecretRetired(Exception):
    """The secret is retired — enable it before replacing or revealing it."""

    def __init__(self, name: str) -> None:
        super().__init__(f"secret {name} is retired")
        self.name = name


class SecretRevisionConflict(Exception):
    """A replace read a revision the store no longer holds — names the current one."""

    def __init__(self, name: str, *, current: int) -> None:
        super().__init__(f"secret {name} is at revision {current}")
        self.name = name
        self.current = current


class SecretReferenced(Exception):
    """Active configured records still name the secret — retire or repoint them first."""

    def __init__(self, name: str, referrers: list[RecordRef]) -> None:
        super().__init__(f"secret {name} is referenced by {', '.join(str(r) for r in referrers)}")
        self.name = name
        self.referrers = referrers


class SecretUnreadable(Exception):
    """A sealed value failed to open: its key generation is absent or it was tampered
    with, moved to another row, or replayed at another revision."""

    def __init__(self, name: str, *, key_id: str) -> None:
        super().__init__(f"secret {name} cannot be opened under key generation {key_id}")
        self.name = name
        self.key_id = key_id


class SecretValueBlank(ValueError):
    """A written secret value is empty or only whitespace — never a usable credential."""

    def __init__(self) -> None:
        super().__init__("a secret value must not be blank")


class SecretRotationConflict(Exception):
    """A row changed between a rotation's read and its commit — nothing was re-sealed."""

    def __init__(self, name: str) -> None:
        super().__init__(f"secret {name} changed during key rotation; nothing was re-sealed — re-run it")
        self.name = name


@domain_model
@dataclass(frozen=True)
class SecretName:
    """A validated secret name — the only way to obtain one is :meth:`parse`."""

    value: str

    @classmethod
    def parse(cls, raw: str) -> SecretName:
        if not raw or not _SLUG_PATTERN.match(raw):
            raise SecretNameError(f"secret name must match [a-z0-9-]+, got {raw!r}")
        return cls(raw)


class SecretValue:
    """A plaintext credential. Redacted in ``repr``/``str``; :meth:`expose` is the one
    accessor, called only where the value is handed to an external system."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    @classmethod
    def entered(cls, raw: str) -> SecretValue:
        """A value entering through a create or replace — :class:`SecretValueBlank` when blank.
        A stored value opens through the constructor, so one sealed before the rule still reveals."""
        if not raw.strip():
            raise SecretValueBlank()
        return cls(raw)

    def expose(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return _REDACTED

    __str__ = __repr__


@domain_model
@dataclass(frozen=True)
class SecretMetadata:
    """A secret's metadata — never its value or ciphertext."""

    name: str
    revision: int
    replaced_at: datetime
    replaced_by: str
    created_at: datetime


def require_revealable(name: str, *, retired: bool) -> None:
    """A retired secret is never revealed — enable it first."""
    if retired:
        raise SecretRetired(name)


def require_unreferenced(name: str, referrers: list[RecordRef]) -> None:
    """A secret an active configured record still names cannot be retired."""
    if referrers:
        raise SecretReferenced(name, referrers)


def listed(
    records: Iterable[SecretMetadata], retired_names: Collection[str], *, include_retired: bool
) -> list[SecretMetadata]:
    """The secrets a listing shows — retired ones only when asked for."""
    return [record for record in records if include_retired or record.name not in retired_names]


def uncovered_key_ids(in_use: Collection[str], available: Collection[str]) -> frozenset[str]:
    """Every key generation a stored row is sealed under that no available generation answers."""
    return frozenset(in_use) - frozenset(available)


def stale_seals(rows: Sequence[SealedSecret], target_key_id: str) -> list[SealedSecret]:
    """The rows a rotation into ``target_key_id`` re-seals: every row sealed under another generation."""
    return [row for row in rows if row.sealed.key_id != target_key_id]


@domain_model
@dataclass(frozen=True)
class KeyGeneration:
    """One hub-key generation: its fingerprint id and its 32 bytes of key material."""

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


# --- Seams (I-prefix, read/write split — bzh:repository-split) ---------------


class ISecretCatalog(Protocol):
    """Read-only secret metadata; it never yields a ciphertext or nonce."""

    def get(self, name: str) -> SecretMetadata | None: ...

    def get_many(self, names: list[str]) -> dict[str, SecretMetadata]:
        """Every named secret that exists, keyed by name (``bzh:bulk-reconstitution``)."""
        ...

    def list_all(self) -> list[SecretMetadata]: ...

    def is_retired(self, name: str) -> bool:
        """Whether ``name``'s newest lifecycle fact reads retired; ``False`` with none."""
        ...

    def retired_names(self) -> set[str]: ...

    def key_ids_in_use(self) -> set[str]:
        """Every distinct ``key_id`` any row is sealed under, retired rows included."""
        ...


class ISealedSecretRepository(Protocol):
    """Read-only access to a secret's sealed row — its ciphertext and nonce."""

    def get_sealed(self, name: str) -> SealedSecret | None: ...


@domain_model
@dataclass(frozen=True)
class Reseal:
    """One row's value re-sealed under another key generation at the same revision."""

    name: str
    revision: int
    from_key_id: str
    sealed: SealedValue


class IResealSecretRepository(ISealedSecretRepository, Protocol):
    """Adds whole-store re-sealing under another key generation."""

    def list_sealed(self) -> list[SealedSecret]: ...

    def reseal(self, changes: list[Reseal]) -> None:
        """Apply every change in one transaction, each a compare-and-set on
        ``(revision, key_id)``; :class:`SecretRotationConflict` on any miss, with nothing
        applied. Moves only ``key_id``, ``ciphertext``, and ``nonce``."""
        ...


class IWriteSecretRepository(ISecretCatalog, Protocol):
    """Adds the secret writes."""

    def create(self, name: str, *, sealed: SealedValue, at: datetime, by: str, change: ConfigChange) -> SecretMetadata:
        """Insert at revision 1 and commit ``change`` with it;
        :class:`SecretAlreadyExists` when the name is taken."""
        ...

    def replace(
        self, name: str, *, from_revision: int, sealed: SealedValue, at: datetime, by: str, change: ConfigChange
    ) -> SecretMetadata:
        """Compare-and-set ``from_revision`` → ``from_revision + 1``, committing ``change``
        with it; :class:`SecretRevisionConflict` when the stored revision has moved."""
        ...

    def record_lifecycle(self, name: str, *, retired: bool, at: datetime, by: str, change: ConfigChange) -> None:
        """Append the lifecycle fact and ``change`` in one transaction. Retiring is refused
        with :class:`SecretReferenced` when an active record names the secret, checked in
        that same transaction."""
        ...


class IHubKeyProvider(Protocol):
    """The hub key's generations, held outside the store (``bzh:pluggable-seams``)."""

    def current(self) -> KeyGeneration: ...

    def generation(self, key_id: str) -> KeyGeneration | None: ...

    def available_ids(self) -> frozenset[str]: ...


class ISecretCipher(Protocol):
    """Authenticated sealing bound to ``(name, revision)``."""

    def seal(self, value: SecretValue, *, name: str, revision: int) -> SealedValue: ...

    def open(self, secret: SealedSecret) -> SecretValue:
        """Raises :class:`SecretUnreadable` on a missing generation or failed tag."""
        ...


class ISecretReader(Protocol):
    """The one decryption path — injected only into objects that call external systems."""

    def reveal(self, name: SecretName) -> SecretValue:
        """Raises :class:`SecretNotFound` or :class:`SecretRetired`."""
        ...
