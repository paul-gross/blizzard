"""Tenant administration — a hub administrator creates, renames and lists the hub's tenants
(``blizzard-product:/plans/multi-tenancy/hub/spec/administration.md`` §Verbs, §Creating a tenant).

A tenant is created in one write transaction: its ``tenants`` row, the packaged graphs minted into it, its built-in
``hub`` work source's sequence, its ``default`` project (``blizzard-product:/plans/projects/hub/spec/model.md`` §Every
tenant starts with a project), and — when one is named — its first admin's membership. Nothing of a tenant whose
creation failed survives, and a tenant is usable the moment its creation commits. A tenant's name is a label of 1 to 64
characters with no leading or trailing whitespace: it is never refused for being taken, and never stands in for the
tenant's id. The concrete store lives at ``src/blizzard/hub/store/internal/tenant_admin_store.py``
(``bzh:dependency-inversion``)."""

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
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.graph.model import IWriteGraphRepository
from blizzard.hub.domain.projects.color import ColorPick, least_used
from blizzard.hub.domain.projects.model import IWriteProjectRepository, Project
from blizzard.hub.tenancy import TenantId, UnknownTenant


class TenantState(StrEnum):
    """Whether a tenant is open, or closed with its deletion underway."""

    OPEN = "open"
    DELETING = "deleting"


@domain_model
@dataclass(frozen=True)
class Tenant:
    """One tenant as its administrators see it: its id, its name, when and by whom it was created, its state, and —
    while its deletion is underway — when it closed."""

    tenant_id: TenantId
    name: str
    created_at: datetime
    created_by: str
    state: TenantState
    closed_at: datetime | None = None


#: The most characters a tenant's name holds (``blizzard-product:/plans/multi-tenancy/hub/spec/store.md`` §The tenant
#: record).
TENANT_NAME_MAX_LENGTH = 64


class TenantNameRefused(ValueError):
    """Raised when a tenant's name is not 1 to 64 characters with no leading or trailing whitespace; nothing is
    written."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"a tenant's name is 1 to {TENANT_NAME_MAX_LENGTH} characters with no leading or trailing whitespace"
        )
        self.name = name


def tenant_name(name: str) -> str:
    """``name``, when it has a tenant name's shape — 1 to 64 characters, no leading or trailing whitespace; else
    :class:`TenantNameRefused`. Inner whitespace is kept, and two tenants may share a name."""
    if not name or len(name) > TENANT_NAME_MAX_LENGTH or name != name.strip():
        raise TenantNameRefused(name)
    return name


class TenantAdminUnknown(LookupError):
    """Raised when the user named as a new tenant's first admin does not exist; the tenant is not created."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"no user {user_id!r} to make the new tenant's admin")
        self.user_id = user_id


class IReadTenantDirectory(Protocol):
    """The hub's tenants, read hub-wide."""

    def list_tenants(self) -> list[Tenant]:
        """Every tenant the hub holds — open, or with its deletion underway and the time it closed — by id, which is
        creation order to the millisecond."""
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

    @property
    def projects(self) -> IWriteProjectRepository:
        """The new tenant's projects, which its default project is written into."""
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


#: The slug and display name of the project every tenant starts with. Nothing else sets it apart: it is renamed, and
#: its slug moved, like any other project.
DEFAULT_PROJECT_SLUG = "default"
DEFAULT_PROJECT_NAME = "Default"


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
        pick: ColorPick,
        clock: IClock,
    ) -> None:
        self._pick = pick
        self._tenants = tenants
        self._mint_packaged = mint_packaged
        self._changed = changed
        self._clock = clock

    def create(self, name: str, *, admin_user_id: str | None, by: str, door: Door = Door.API) -> Tenant:
        """Create a tenant named ``name`` in one transaction, holding its default project, ``admin_user_id`` its first
        admin when named. The creator is granted nothing they did not name themselves for; the project's ``create``
        change names the creator and ``door``. :class:`TenantNameRefused` when ``name`` is not a tenant name's shape —
        nothing is created."""
        name = tenant_name(name)
        at = self._clock.now()
        tenant = TenantId(Id.mint(IdPrefix.TENANT, self._clock).value)
        with self._tenants.create(tenant, name=name, at=at, by=by) as creation:
            self._mint_packaged(creation.graphs)
            creation.seed_hub_sequence()
            project, change = Project.new(
                DEFAULT_PROJECT_SLUG,
                DEFAULT_PROJECT_NAME,
                least_used((), self._pick),
                ChangeContext(actor=by, door=door),
                at=at,
            )
            creation.projects.create(project, change=change)
            if admin_user_id is not None:
                creation.grant_admin(admin_user_id, by=by, at=at)
        self._changed()
        return Tenant(tenant_id=tenant, name=name, created_at=at, created_by=by, state=TenantState.OPEN)

    def rename(self, tenant: TenantId, name: str) -> Tenant:
        """Rename an open tenant; :class:`UnknownTenant` when no open tenant has the id, :class:`TenantNameRefused` when
        ``name`` is not a tenant name's shape — nothing is renamed."""
        renamed = self._tenants.rename(tenant, tenant_name(name))
        if renamed is None:
            raise UnknownTenant(tenant)
        self._changed()
        return renamed

    def list_tenants(self) -> list[Tenant]:
        return self._tenants.list_tenants()


__all__ = [
    "DEFAULT_PROJECT_NAME",
    "DEFAULT_PROJECT_SLUG",
    "TENANT_NAME_MAX_LENGTH",
    "IReadTenantDirectory",
    "ITenantCreation",
    "IWriteTenantDirectory",
    "Tenant",
    "TenantAdminUnknown",
    "TenantAdministration",
    "TenantNameRefused",
    "TenantState",
    "tenant_name",
]
