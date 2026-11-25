"""The cadence area's registrations: its ports over the store's adapters, its services, the sweep's tenant half, the
clearing of the chore overrides a retiring urgency names, and the one sequence of every area's clearing the urgency
lifecycle takes. The sweep is one per process, reaching each tenant's half through the :class:`TenantCadences`
accessor the root supplies, and mints only while the ``cadences`` flag is on. Cadence may import garden, so it composes
the routine clearing with its own; the urgency area declares only the key. The store-free graph declares both
absent."""

from __future__ import annotations

from typing import Any, cast

from blizzard.foundation.flags import Flags
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.cadence.mint import CadenceMint, ErrandPlanning
from blizzard.hub.domain.cadence.ports import (
    IReadCadenceCorpus,
    IReadCadenceRepository,
    IReadErrandRunsRepository,
    IWriteCadenceRepository,
)
from blizzard.hub.domain.cadence.services import (
    CadenceAttention,
    CadenceAuthoring,
    CadenceLifecycle,
    CadenceReading,
    CadenceUrgencyClearing,
)
from blizzard.hub.domain.cadence.sweep import CadenceSweep, TenantCadence, TenantCadences
from blizzard.hub.domain.garden.routines import RoutineUrgencyClearing
from blizzard.hub.domain.urgency.ports import UrgencyReferenceClearings
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def urgency_reference_clearings(
    routines: RoutineUrgencyClearing, cadences: CadenceUrgencyClearing
) -> UrgencyReferenceClearings:
    """Every clearing a retire runs: the routine defaults, then the chore overrides."""
    return (routines, cadences)


def cadence_sweep(corpus: IReadCadenceCorpus, cadences: TenantCadences, flags: Flags) -> CadenceSweep:
    """The cadence sweep's one pass per hub, minting only while the ``cadences`` flag is on."""
    return CadenceSweep(corpus=corpus, tenant=cadences, minting=lambda: flags.on("cadences"))


#: The process keys the sweep declares.
_PROCESS_KEYS: tuple[type[Any], ...] = (TenantCadences, CadenceSweep)


#: The ports the area aliases onto the adapters it is handed.
_PORTS: tuple[type[Any], ...] = (IReadCadenceRepository, IWriteCadenceRepository, IReadErrandRunsRepository)

#: The services the area declares once per tenant, beside the chore clearing.
_SERVICES: tuple[type[Any], ...] = (
    CadenceAttention,
    CadenceReading,
    CadenceAuthoring,
    CadenceLifecycle,
    ErrandPlanning,
    CadenceMint,
    TenantCadence,
)


def register(
    registry: Registry, *, cadences: Ref[IWriteCadenceRepository], runs: Ref[IReadErrandRunsRepository]
) -> Ref[CadenceSweep]:
    """Declare the area's ports over the adapters handed in, its services, the chore clearing and the composed
    clearings, each once per tenant; and the sweep and the accessor it reaches each tenant through, once per process,
    handing back the sweep's ``Ref``."""
    registry.alias(port(IReadCadenceRepository), cadences)
    registry.alias(port(IWriteCadenceRepository), cadences)
    registry.alias(port(IReadErrandRunsRepository), runs)
    for service in _SERVICES:
        registry.service(service, lifetime=Lifetime.TENANT)
    registry.service(CadenceUrgencyClearing, lifetime=Lifetime.TENANT)
    registry.factory(urgency_reference_clearings, lifetime=Lifetime.TENANT)
    registry.context(port(TenantCadences), lifetime=Lifetime.PROCESS)
    return registry.factory(cadence_sweep, lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports, services, chore clearing, composed clearings and sweep absent, for the store-free
    graph."""
    for missing in (*_PORTS, *_SERVICES):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    registry.absent(port(CadenceUrgencyClearing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    registry.absent(
        port(cast("type[Any]", UrgencyReferenceClearings)), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE
    )
    for missing in _PROCESS_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
