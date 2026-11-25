"""The tenancy area's registrations: tenant administration, tenant teardown and the teardown sweep, once per process,
over the store's tenant directory, deletion facts and closed-tenant units the root hands in.

The area declares the keys it needs and higher wiring provides: the tenant-set-changed notice, from the wiring that
holds the open tenants; the packaged-graph mint, from the wiring that owns graph sync; and the closed-tenant
forgetting, a process value the root supplies and binds over its tenant scopes. The store-free graph declares every
key absent."""

from __future__ import annotations

from typing import Any, cast

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.observability.hub_events import HubEventLogService
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.domain.tenancy.administration import (
    IWriteTenantDirectory,
    PackagedGraphMint,
    TenantAdministration,
    TenantSetChanged,
    TenantUnits,
)
from blizzard.hub.domain.tenancy.teardown import (
    ClosedTenantForgetting,
    IReadTenantTeardowns,
    IWriteTenantTeardowns,
    TenantTeardown,
    TenantTeardownSweep,
)
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def tenant_administration(
    directory: IWriteTenantDirectory,
    units: TenantUnits,
    mint: PackagedGraphMint,
    changed: TenantSetChanged,
    pick: ColorPick,
    clock: IClock,
) -> TenantAdministration:
    """Creates, renames and lists the hub's tenants, telling ``changed`` after each committed change."""
    return TenantAdministration(
        tenants=directory, units=units, mint_packaged=mint, changed=changed, pick=pick, clock=clock
    )


def tenant_teardown(
    teardowns: IWriteTenantTeardowns,
    units: TenantUnits,
    forgetting: ClosedTenantForgetting,
    events: HubEventLogService,
    clock: IClock,
) -> TenantTeardown:
    """Deletes tenants, forgetting each one once its close commits."""
    return TenantTeardown(teardowns=teardowns, units=units, closed=forgetting, events=events, clock=clock)


def tenant_teardown_sweep(teardown: TenantTeardown, teardowns: IReadTenantTeardowns) -> TenantTeardownSweep:
    """Carries on every deletion underway."""
    return TenantTeardownSweep(teardown=teardown, teardowns=teardowns)


#: The ports the area aliases onto the adapters it is handed.
_PORTS: tuple[type[Any], ...] = (IWriteTenantDirectory, IWriteTenantTeardowns, IReadTenantTeardowns)

#: The services the area declares once per process.
_SERVICES: tuple[type[Any], ...] = (TenantAdministration, TenantTeardown, TenantTeardownSweep)


def register(
    registry: Registry,
    *,
    directory: Ref[IWriteTenantDirectory],
    teardowns: Ref[IWriteTenantTeardowns],
    units: Ref[TenantUnits],
) -> Ref[TenantTeardownSweep]:
    """Declare the area's ports over the adapters handed in, the closed-tenant forgetting the root supplies, and the
    area's services, each once per process, handing back the teardown sweep's ``Ref``."""
    registry.alias(port(IWriteTenantDirectory), directory)
    registry.alias(port(IWriteTenantTeardowns), teardowns)
    registry.alias(port(IReadTenantTeardowns), teardowns)
    registry.alias(port(cast("type[Any]", TenantUnits)), units)
    registry.context(port(ClosedTenantForgetting), lifetime=Lifetime.PROCESS)
    registry.factory(tenant_administration, lifetime=Lifetime.PROCESS)
    registry.factory(tenant_teardown, lifetime=Lifetime.PROCESS)
    return registry.factory(tenant_teardown_sweep, lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports, keys and services absent, for the store-free graph. The keys higher wiring provides
    are declared absent by their providers."""
    for missing in (*_PORTS, *_SERVICES, ClosedTenantForgetting):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    registry.absent(port(cast("type[Any]", TenantUnits)), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
