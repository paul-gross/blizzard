"""The host's identity at its hub — the hub-minted ``hs_`` id and the tenant its first answered report named.

A host is registered when it holds a host token; the identity is what the hub answered for that token. It is recorded
once, from the first answered report, in the store's single ``host_identity`` row, and never edited after: a later
answer naming another host or tenant is a refusal of the host's credential, not an update. Every consumer reads the
process holder (:class:`ICurrentHostIdentity`), seeded from the row at boot and handed the identity once the row is
written."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class HostIdentity:
    """Who this host is at its hub: the hub-minted ``hs_`` id, the tenant the hub answered with, and when the first
    answered report was recorded, by the host's clock."""

    host_id: str
    tenant_id: str
    registered_at: datetime

    def same_host(self, host_id: str, tenant_id: str) -> bool:
        """Whether an answer naming ``host_id`` in ``tenant_id`` is this identity."""
        return (self.host_id, self.tenant_id) == (host_id, tenant_id)


class HostIdentityAlreadyRecorded(RuntimeError):
    """The store already holds a host identity other than the one offered: the row is written once and never edited."""

    def __init__(self, held: HostIdentity, offered: HostIdentity) -> None:
        super().__init__(
            f"host identity already recorded as {held.host_id} in {held.tenant_id}; "
            f"refusing {offered.host_id} in {offered.tenant_id}"
        )
        self.held = held
        self.offered = offered


class IReadHostIdentityRepository(Protocol):
    """Read the host store's single identity row."""

    def host_identity(self) -> HostIdentity | None:
        """The identity the host's first answered report recorded, or ``None`` before one."""
        ...


class IWriteHostIdentityRepository(IReadHostIdentityRepository, Protocol):
    """Read-write access to the identity row — held only by the host's registration step."""

    def record_host_identity(self, identity: HostIdentity) -> None:
        """Write the single identity row as ``identity``, in one transaction. Offering the identity the row already
        holds writes nothing; offering any other raises :class:`HostIdentityAlreadyRecorded`."""
        ...


class ICurrentHostIdentity(Protocol):
    """The process's current host identity."""

    def current(self) -> HostIdentity | None:
        """The identity this process knows the host by, or ``None`` before its first answered report."""
        ...


class HostIdentityHolder:
    """The process-lifetime :class:`ICurrentHostIdentity`, seeded with the row's identity at boot and handed the
    identity once the row is first written. Read by the host tick and the request threads at once."""

    def __init__(self, identity: HostIdentity | None = None) -> None:
        self._lock = threading.Lock()
        self._identity = identity

    def current(self) -> HostIdentity | None:
        with self._lock:
            return self._identity

    def hold(self, identity: HostIdentity) -> None:
        """Answer ``identity`` from now on — called after the store records it, never before."""
        with self._lock:
            self._identity = identity


def _conforms_current_host_identity(x: HostIdentityHolder) -> ICurrentHostIdentity:
    return x
