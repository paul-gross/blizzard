"""The sweeps area's registrations: the pass locks, and the roster of every sweep this hub runs, once per process.

The roster's members are born in the areas that own them, and this area imports none of them. The root hands each
member's ``Ref`` in, typed by the pass this area runs it through, and the area keys each under its own role; the trace
and egress exports' sweeps arrive as ``None`` while their export is off, and the roster then leaves them out. The
store-free graph declares every key absent."""

from __future__ import annotations

from typing import Any, Protocol, cast

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.sweeps.locks import SweepLocks
from blizzard.hub.domain.sweeps.names import SweepName
from blizzard.hub.domain.sweeps.roster import ISweepsEveryTenant, ISweepsOneTenant, SweepRoster
from blizzard.hub.tenancy import STORE_UNAVAILABLE


class ISweepsEitherWay(ISweepsEveryTenant, ISweepsOneTenant, Protocol):
    """A sweep that passes over every tenant, or over one."""


class IForgeStatusSweep(ISweepsEitherWay, Protocol):
    """The ``forge_status`` sweep, as the roster holds it."""


class IEventDerivationSweep(ISweepsEitherWay, Protocol):
    """The ``event_derivation`` sweep, as the roster holds it."""


class ICloseDrainSweep(ISweepsEitherWay, Protocol):
    """The ``close_drain`` sweep, as the roster holds it."""


class ICadenceSweep(ISweepsEitherWay, Protocol):
    """The ``cadence`` sweep, as the roster holds it."""


class ITenantTeardownSweep(ISweepsEveryTenant, Protocol):
    """The ``tenant_teardown`` sweep, as the roster holds it."""


class ITraceExportSweep(ISweepsEveryTenant, Protocol):
    """The ``trace_export`` sweep, as the roster holds it."""


class IEgressSweep(ISweepsEveryTenant, Protocol):
    """The ``egress`` sweep, as the roster holds it."""


def sweep_roster(
    forge_status: IForgeStatusSweep,
    event_derivation: IEventDerivationSweep,
    close_drain: ICloseDrainSweep,
    cadence: ICadenceSweep,
    tenant_teardown: ITenantTeardownSweep,
    trace_export: ITraceExportSweep | None,
    egress: IEgressSweep | None,
    locks: SweepLocks,
    clock: IClock,
) -> SweepRoster:
    """Every sweep this hub runs by name, each pass under its lock; an export's sweep only while its export runs."""
    either_way: dict[SweepName, ISweepsEitherWay] = {
        SweepName.FORGE_STATUS: forge_status,
        SweepName.EVENT_DERIVATION: event_derivation,
        SweepName.CLOSE_DRAIN: close_drain,
        SweepName.CADENCE: cadence,
    }
    every_tenant: dict[SweepName, ISweepsEveryTenant] = {**either_way, SweepName.TENANT_TEARDOWN: tenant_teardown}
    if trace_export is not None:
        every_tenant[SweepName.TRACE_EXPORT] = trace_export
    if egress is not None:
        every_tenant[SweepName.EGRESS] = egress
    return SweepRoster(every_tenant=every_tenant, one_tenant=dict(either_way), locks=locks, clock=clock)


#: The role each member is keyed under.
_ROLES: tuple[type[Any], ...] = (
    IForgeStatusSweep,
    IEventDerivationSweep,
    ICloseDrainSweep,
    ICadenceSweep,
    ITenantTeardownSweep,
)

#: The roles of the members an off export leaves out.
_EXPORT_ROLES: tuple[type[Any], ...] = (ITraceExportSweep, IEgressSweep)


def register(
    registry: Registry,
    *,
    forge_status: Ref[IForgeStatusSweep],
    event_derivation: Ref[IEventDerivationSweep],
    close_drain: Ref[ICloseDrainSweep],
    cadence: Ref[ICadenceSweep],
    tenant_teardown: Ref[ITenantTeardownSweep],
    trace_export: Ref[ITraceExportSweep] | None,
    egress: Ref[IEgressSweep] | None,
) -> None:
    """Key each member handed in under its role, an export's sweep as ``None`` when it is handed none, and declare the
    locks and the roster over them, once per process."""
    registry.alias(port(IForgeStatusSweep), forge_status)
    registry.alias(port(IEventDerivationSweep), event_derivation)
    registry.alias(port(ICloseDrainSweep), close_drain)
    registry.alias(port(ICadenceSweep), cadence)
    registry.alias(port(ITenantTeardownSweep), tenant_teardown)
    registry.optional(port(ITraceExportSweep), trace_export, lifetime=Lifetime.PROCESS)
    registry.optional(port(IEgressSweep), egress, lifetime=Lifetime.PROCESS)
    registry.service(SweepLocks, lifetime=Lifetime.PROCESS)
    registry.factory(sweep_roster, lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the roles, the locks and the roster absent, for the store-free graph."""
    for missing in (*_ROLES, SweepLocks, SweepRoster):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    for role in _EXPORT_ROLES:
        registry.absent(port(cast("type[Any]", role | None)), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
