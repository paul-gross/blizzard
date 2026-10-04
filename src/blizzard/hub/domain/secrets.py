"""Secret domain model — a named credential written once, sealed at rest, never returned.

A value enters through :class:`SecretAuthoring`, is sealed under the hub key by an
:class:`ISecretCipher`, and leaves only through :class:`ISecretReader` as a
:class:`SecretValue` whose ``repr``/``str`` are redacted (``bzh:secret-write-only``).
Retire/enable is a newest-fact-wins brake, a scope's shape (``bzh:facts-not-status``)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock

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


class SecretUnreadable(Exception):
    """A sealed value failed to open: its key generation is absent or it was tampered
    with, moved to another row, or replayed at another revision."""

    def __init__(self, name: str, *, key_id: str) -> None:
        super().__init__(f"secret {name} cannot be opened under key generation {key_id}")
        self.name = name
        self.key_id = key_id


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

    def expose(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return _REDACTED

    __str__ = __repr__


@dataclass(frozen=True)
class SecretRecord:
    """A secret's metadata — never its value or ciphertext."""

    name: str
    revision: int
    replaced_at: datetime
    replaced_by: str
    created_at: datetime


@dataclass(frozen=True)
class KeyGeneration:
    """One hub-key generation: its fingerprint id and its 32 bytes of key material."""

    key_id: str
    material: bytes = field(repr=False)


@dataclass(frozen=True)
class SealedValue:
    """A value sealed under one key generation for one ``(name, revision)``."""

    key_id: str
    ciphertext: bytes
    nonce: bytes


@dataclass(frozen=True)
class SealedSecret:
    name: str
    revision: int
    sealed: SealedValue


# --- Seams (I-prefix, read/write split — bzh:repository-split) ---------------


class ISecretCatalog(Protocol):
    """Secret metadata. Controllers depend on this variant; it never yields a
    ciphertext or nonce."""

    def get(self, name: str) -> SecretRecord | None: ...

    def get_many(self, names: list[str]) -> dict[str, SecretRecord]:
        """Every named secret that exists, keyed by name (``bzh:bulk-reconstitution``)."""
        ...

    def list_all(self) -> list[SecretRecord]: ...

    def is_retired(self, name: str) -> bool:
        """Whether ``name``'s newest lifecycle fact reads retired; ``False`` with none."""
        ...

    def retired_names(self) -> set[str]: ...

    def key_ids_in_use(self) -> set[str]:
        """Every distinct ``key_id`` any row is sealed under, retired rows included."""
        ...


class ISealedSecretRepository(Protocol):
    """Sealed rows. Held by the reader, key rotation, and nothing on the request plane."""

    def get_sealed(self, name: str) -> SealedSecret | None: ...


class IWriteSecretRepository(ISecretCatalog, Protocol):
    """Secret writes. Only the domain services below depend on this variant."""

    def create(self, name: str, *, sealed: SealedValue, at: datetime, by: str) -> SecretRecord:
        """Insert at revision 1; :class:`SecretAlreadyExists` when the name is taken."""
        ...

    def replace(self, name: str, *, from_revision: int, sealed: SealedValue, at: datetime, by: str) -> SecretRecord:
        """Compare-and-set ``from_revision`` → ``from_revision + 1``;
        :class:`SecretRevisionConflict` when the stored revision has moved."""
        ...

    def record_lifecycle(self, name: str, *, retired: bool, at: datetime, by: str) -> None: ...


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


# --- Domain services ------------------------------------------------------------


class SecretAuthoring:
    """Create and replace a secret's value. The plaintext arrives as a ``str`` so no
    request-plane module has to import :class:`SecretValue`; it is wrapped on entry."""

    def __init__(self, *, secrets: IWriteSecretRepository, cipher: ISecretCipher, clock: IClock) -> None:
        self._secrets = secrets
        self._cipher = cipher
        self._clock = clock

    def create(self, name: SecretName, value: str, *, by: str) -> SecretRecord:
        sealed = self._cipher.seal(SecretValue(value), name=name.value, revision=1)
        return self._secrets.create(name.value, sealed=sealed, at=self._clock.now(), by=by)

    def replace(self, record: SecretRecord, value: str, *, by: str, if_match: int | None = None) -> SecretRecord:
        """Seal under ``record.revision + 1`` and compare-and-set from ``record.revision``.
        ``if_match`` is the revision the caller last saw, checked before any write."""
        if self._secrets.is_retired(record.name):
            raise SecretRetired(record.name)
        if if_match is not None and if_match != record.revision:
            raise SecretRevisionConflict(record.name, current=record.revision)
        sealed = self._cipher.seal(SecretValue(value), name=record.name, revision=record.revision + 1)
        return self._secrets.replace(
            record.name, from_revision=record.revision, sealed=sealed, at=self._clock.now(), by=by
        )


class SecretLifecycle:
    """Set or clear a secret's retired brake without touching its row."""

    def __init__(self, *, secrets: IWriteSecretRepository, clock: IClock) -> None:
        self._secrets = secrets
        self._clock = clock

    def retire(self, record: SecretRecord, *, by: str) -> None:
        self._secrets.record_lifecycle(record.name, retired=True, at=self._clock.now(), by=by)

    def enable(self, record: SecretRecord, *, by: str) -> None:
        self._secrets.record_lifecycle(record.name, retired=False, at=self._clock.now(), by=by)
