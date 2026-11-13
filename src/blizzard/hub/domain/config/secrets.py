"""Secret domain model — a named credential written once, sealed at rest, never returned.

A value is sealed under the hub key by an ``ISecretCipher``, and leaves only through
:class:`ISecretReader` as a :class:`SecretValue` whose ``repr``/``str`` are redacted
(``bzh:secret-write-only``). Retire/enable is a newest-fact-wins brake (``bzh:facts-not-status``)."""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.secrets.sealing import SealedValue, uncovered_key_ids
from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.domain.config.changes import ConfigChange, RecordRef
from blizzard.hub.domain.kernel.locks import LockKey, LockNamespace
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction

_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")


def secret_lock(name: str) -> LockKey:
    """The keyed lock on the secret ``name`` (``bzh:transaction-graduation``): a unit writing the secret, or a record
    naming it, declares it."""
    return LockKey(LockNamespace.SECRET, name)


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


class KeysUncovered(Exception):
    """Stored secrets are sealed under key generations the hub key source does not hold."""

    def __init__(self, missing: frozenset[str]) -> None:
        super().__init__(f"stored secrets are sealed under key generation(s) {sorted(missing)} no key answers")
        self.missing = missing


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


@domain_model
@dataclass(frozen=True)
class SecretMetadata:
    """A secret's metadata — never its value or ciphertext. ``retired`` is whether its newest
    lifecycle fact reads retired, as of the load."""

    name: str
    revision: int
    replaced_at: datetime
    replaced_by: str
    created_at: datetime
    retired: bool


def require_revealable(name: str, *, retired: bool) -> None:
    """A retired secret is never revealed — enable it first."""
    if retired:
        raise SecretRetired(name)


def retirable(referrers: Sequence[RecordRef], *, retired: bool) -> bool:
    """Whether retiring the secret would be accepted: an already-retired one is a no-op,
    a live one needs no active configured record still naming it."""
    return retired or not referrers


def require_unreferenced(name: str, referrers: list[RecordRef]) -> None:
    """A secret an active configured record still names cannot be retired."""
    if not retirable(referrers, retired=False):
        raise SecretReferenced(name, referrers)


def listed(
    records: Iterable[SecretMetadata], retired_names: Collection[str], *, include_retired: bool
) -> list[SecretMetadata]:
    """The secrets a listing shows — retired ones only when asked for."""
    return [record for record in records if include_retired or record.name not in retired_names]


def require_keys_covered(in_use: Collection[str], available: Collection[str]) -> None:
    """Refuse to start rather than serve secrets no generation can open."""
    missing = uncovered_key_ids(in_use, available)
    if missing:
        raise KeysUncovered(missing)


# --- Seams (I-prefix, read/write split — bzh:repository-split) ---------------


class ISecretCatalog(Protocol):
    """Read-only secret metadata; it never yields a ciphertext or nonce."""

    def get(self, name: str) -> SecretMetadata | None: ...

    def get_many(self, names: list[str], *, transaction: Transaction | None = None) -> dict[str, SecretMetadata]:
        """Every named secret that exists, keyed by name (``bzh:bulk-reconstitution``) — read in ``transaction`` when
        one is given (``bzh:transaction-participation``)."""
        ...

    def list_all(self) -> list[SecretMetadata]: ...

    def is_retired(self, name: str) -> bool:
        """Whether ``name``'s newest lifecycle fact reads retired; ``False`` with none."""
        ...

    def retired_names(self, names: Sequence[str] | None = None) -> set[str]:
        """Every name whose newest lifecycle fact reads retired — of ``names`` alone when given, a name with no
        fact dropped (``bzh:bulk-reconstitution``)."""
        ...

    def key_ids_in_use(self) -> set[str]:
        """Every distinct ``key_id`` any row is sealed under, retired rows included."""
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

    def record_lifecycle(
        self, name: str, *, retired: bool, at: datetime, by: str, change: ConfigChange, transaction: LockedTransaction
    ) -> None:
        """Append the lifecycle fact and ``change`` in ``transaction``, which locks the secret."""
        ...


class ISecretReader(Protocol):
    """The one decryption path — injected only into objects that call external systems."""

    def reveal(self, name: SecretName) -> SecretValue:
        """Raises :class:`SecretNotFound` or :class:`SecretRetired`."""
        ...
