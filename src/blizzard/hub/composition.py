"""The hub's construction root for the borrowed context ``hub host`` supplies — the settings graph, the store engine and
connections, and the system clock — and for the hosted hub's flow driver over an opened :class:`HubProcess`
(``bzh:dependency-injection``). Every other collaborator is the process container's, born in its area's wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.api.chunk_events import AnnouncedTurns
from blizzard.hub.delivery.flow_driver import FlowDriver, FlowLineStores, ITurnRunner, StepWake
from blizzard.hub.domain.delivery.ports.flows import IFlowChangePublisher, IReadFlowLinesCorpus
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.observability.egress.boot import EgressBoot, EgressPathKey
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.egress.factory import egress_boot
from blizzard.hub.forge.budget_notices import BudgetNoticeDrain
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.settings.footing import HubSettingsFooting
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.settings.reader import HubSettingsReader
from blizzard.hub.settings.service import HubSettingsAuthoring
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.hub_settings_store import HubSettingsStore
from blizzard.hub.store.tenant_guard import install_tenant_guard
from blizzard.hub.tenancy import StoreScope, TenantId

if TYPE_CHECKING:
    from blizzard.hub.process_root import HubProcess


def build_hub_settings(
    connections: HubStoreConnections, clock: IClock, footing: HubSettingsFooting
) -> HubSettingsGraph:
    store = HubSettingsStore(connections, clock, footing.validate)
    reader = HubSettingsReader(store)
    return HubSettingsGraph(store, reader, HubSettingsAuthoring(reader, store, footing.validate), footing)


def connections_for_engine(engine: Engine, platform_tracing: IPlatformTracing | None = None) -> HubStoreConnections:
    """Build the hub store graph over a borrowed engine."""
    if platform_tracing is not None:
        platform_tracing.instrument_engine(engine)
    return HubStoreConnections(engine, HubStoreErrorFactory(get_logger("blizzard.hub.store")))


def open_hub_engine(db_url: str) -> Engine:
    """Open a hub engine only after installing the tenant guard."""
    install_tenant_guard()
    return create_engine_from_url(db_url)


def open_hub_store(db_url: str, platform_tracing: IPlatformTracing | None = None) -> tuple[Engine, HubStoreConnections]:
    """Acquire an engine and its shared, instrumented connections graph."""
    engine = open_hub_engine(db_url)
    try:
        return engine, connections_for_engine(engine, platform_tracing)
    except BaseException:
        engine.dispose()
        raise


def build_system_clock() -> IClock:
    """The one wall clock a hub process reads."""
    return SystemClock()


def build_egress_boot(config: EgressConfig, key: EgressPathKey | None) -> EgressBoot:
    """The fact export as the hub boots it over the boot ``[egress]`` config and the path hash ``key``."""
    return egress_boot(config, key)


def build_flow_driver(process: HubProcess) -> FlowDriver:
    """The hosted hub's flow driver, over every open tenant's lines and turns, bound to the process's step wake. Each
    :class:`FlowLineStores` callable is a named accessor resolving one type from the row's tenant scope — the very
    instance a request beneath that tenant resolves — so the driver holds typed callables and no resolver. Built by
    ``build_hosted_app`` alone, so no CLI or test build over a live hub's store takes that hub's turns."""
    resolver = process.resolver
    open_tenants = resolver.get(OpenTenants)
    tenants = process.tenants

    def reconciler_of(tenant: TenantId) -> FlowLineReconciler:
        return tenants.resolve(StoreScope(tenant), FlowLineReconciler)

    def turns_of(tenant: TenantId) -> ITurnRunner:
        return tenants.resolve(StoreScope(tenant), AnnouncedTurns)

    def notices_of(tenant: TenantId) -> BudgetNoticeDrain:
        return tenants.resolve(StoreScope(tenant), BudgetNoticeDrain)

    def publisher_of(tenant: TenantId) -> IFlowChangePublisher:
        return tenants.resolve(StoreScope(tenant), IFlowChangePublisher)

    driver = FlowDriver(
        FlowLineStores(
            corpus=resolver.get(IReadFlowLinesCorpus),
            tenants=lambda: open_tenants.every().keys(),
            lines=reconciler_of,
            turns=turns_of,
            notices=notices_of,
            publishers=publisher_of,
        ),
        resolver.get(IClock),
    )
    resolver.get(StepWake).bind(driver)
    return driver
