"""Tenant administration — a hub administrator creates, renames and lists the hub's tenants
(``blizzard-product:/plans/multi-tenancy/hub/spec/administration.md`` §Verbs, §Creating a tenant).

A tenant is created in one write transaction: its ``tenants`` row, the packaged graphs minted into it, its built-in
``hub`` work source's sequence, and — when one is named — its first admin's membership. Nothing of a tenant whose
creation failed survives, and a tenant is usable the moment its creation commits. A tenant's name is a label: it is
never refused for being taken, and never stands in for the tenant's id. The concrete store lives at
``src/blizzard/hub/store/internal/tenant_admin_store.py`` (``bzh:dependency-inversion``)."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.graph.model import IWriteGraphRepository
from blizzard.hub.tenancy import TenantId, UnknownTenant


class TenantState(StrEnum):
    """Whether a tenant is open, or closed with its deletion underway."""

    OPEN = "open"
    DELETING = "deleting"


@domain_model
@dataclass(frozen=True)
class Tenant:
    """One tenant as its administrators see it: its id, its name, when and by whom it was created, and its state."""

    tenant_id: TenantId
    name: str
    created_at: datetime
    created_by: str
    state: TenantState


class TenantAdminUnknown(LookupError):
    """Raised when the user named as a new tenant's first admin does not exist; the tenant is not created."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"no user {user_id!r} to make the new tenant's admin")
        self.user_id = user_id


class IReadTenantDirectory(Protocol):
    """The hub's tenants, read hub-wide."""

    def list_tenants(self) -> list[Tenant]:
        """Every tenant the hub holds — open, or with its deletion underway — by id, which is creation order to the
        millisecond."""
        ...


class ITenantCreation(Protocol):
    """The writes one tenant's creation makes, each on the creation's one transaction."""

    @property
    def graphs(self) -> IWriteGraphRepository:
        """The new tenant's graphs, which the packaged graphs are minted into."""
        ...

    def seed_hub_sequence(self) -> None:
        """Seed the new tenant's built-in ``hub`` work source: its sequence row, numbering from 1."""
        ...

    def grant_admin(self, user_id: str, *, by: str, at: datetime) -> None:
        """Make ``user_id`` an ``admin`` member of the new tenant; :class:`TenantAdminUnknown` when no such user
        exists."""
        ...


class IWriteTenantDirectory(IReadTenantDirectory, Protocol):
    """Adds the tenant writes."""

    def create(self, tenant: TenantId, *, name: str, at: datetime, by: str) -> AbstractContextManager[ITenantCreation]:
        """One transaction that inserts ``tenant``'s row and hands out the rest of its creation: committed when the
        block exits, rolled back — the row included — when it raises."""
        ...

    def rename(self, tenant: TenantId, name: str) -> Tenant | None:
        """Rename an open tenant and answer it renamed; ``None`` when no open tenant has the id."""
        ...


class TenantAdministration:
    """Creates, renames and lists the hub's tenants.

    ``mint_packaged`` reconciles the packaged graph set into the graphs it is handed — the same reconciliation the
    hub-level graph sync runs for each tenant. ``changed`` is told after every committed change to the tenant set
    or a tenant's name, so what the hub holds in memory about its open tenants is read again."""

    def __init__(
        self,
        *,
        tenants: IWriteTenantDirectory,
        mint_packaged: Callable[[IWriteGraphRepository], object],
        changed: Callable[[], object],
        clock: IClock,
    ) -> None:
        self._tenants = tenants
        self._mint_packaged = mint_packaged
        self._changed = changed
        self._clock = clock

    def create(self, name: str, *, admin_user_id: str | None, by: str) -> Tenant:
        """Create a tenant named ``name`` in one transaction, ``admin_user_id`` its first admin when named. The
        creator is granted nothing they did not name themselves for."""
        at = self._clock.now()
        tenant = TenantId(Id.mint(IdPrefix.TENANT, self._clock).value)
        with self._tenants.create(tenant, name=name, at=at, by=by) as creation:
            self._mint_packaged(creation.graphs)
            creation.seed_hub_sequence()
            if admin_user_id is not None:
                creation.grant_admin(admin_user_id, by=by, at=at)
        self._changed()
        return Tenant(tenant_id=tenant, name=name, created_at=at, created_by=by, state=TenantState.OPEN)

    def rename(self, tenant: TenantId, name: str) -> Tenant:
        """Rename an open tenant; :class:`UnknownTenant` when no open tenant has the id."""
        renamed = self._tenants.rename(tenant, name)
        if renamed is None:
            raise UnknownTenant(tenant)
        self._changed()
        return renamed

    def list_tenants(self) -> list[Tenant]:
        return self._tenants.list_tenants()


__all__ = [
    "IReadTenantDirectory",
    "ITenantCreation",
    "IWriteTenantDirectory",
    "Tenant",
    "TenantAdminUnknown",
    "TenantAdministration",
    "TenantState",
]
