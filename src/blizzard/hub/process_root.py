"""The hub process's composition root: it registers the hub graph, then opens the validated container over it.

The store-free graph declares each store-backed registration absent, and a graph without settings the settings ports.
The process scope borrows every value it opens with; the :class:`HubProcess` owns only the container, closing its tenant
scopes and then the process scope."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.platform_tracing import wiring as platform_tracing_wiring
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.foundation.registry.interface import port
from blizzard.foundation.registry.internal.translation import HUB_LIFETIMES, build_container
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, Resolver, ScopeValue, ScopeValues, supply
from blizzard.foundation.roles import collaborator
from blizzard.hub.api import wiring as api
from blizzard.hub.composition import register_legacy, register_legacy_absent
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery import wiring as delivery
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.documents import wiring as documents
from blizzard.hub.domain.cadence import wiring as cadence_area
from blizzard.hub.domain.config import wiring as config_wiring
from blizzard.hub.domain.config_apply import wiring as config_apply
from blizzard.hub.domain.garden import wiring as garden_area
from blizzard.hub.domain.garden.findings import wiring as findings
from blizzard.hub.domain.graph import wiring as graph_area
from blizzard.hub.domain.projects import wiring as projects
from blizzard.hub.domain.urgency import wiring as urgency_area
from blizzard.hub.events import wiring as events
from blizzard.hub.forge import wiring as forge
from blizzard.hub.graphs import PACKAGED as GRAPHS_PACKAGED
from blizzard.hub.graphs import PackagedGraphs
from blizzard.hub.live_config import wiring as live_config
from blizzard.hub.scopes import wiring as scopes
from blizzard.hub.scopes.environ import HubEnviron
from blizzard.hub.scopes.tenants import TenantContainers
from blizzard.hub.secrets import wiring as secrets
from blizzard.hub.settings import wiring as settings_wiring
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.store import wiring as store
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.system_artifacts import PACKAGED as SYSTEM_ARTIFACTS_PACKAGED
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.tenancy import TenantId, UnknownTenant
from blizzard.hub.work_sources import wiring as work_sources

#: The process scope's owner id.
HUB_OWNER = "hub"


@collaborator
@dataclass(frozen=True)
class HubProcess:
    """The hub's open process scope and the tenant scopes opened beneath it."""

    owner: OwnedChild
    tenants: TenantContainers

    @property
    def resolver(self) -> Resolver:
        """The process scope's guarded resolver."""
        return self.owner.resolver

    def close(self) -> None:
        """Close every tenant scope, then the process scope, attempting the second when the first fails."""
        try:
            self.tenants.close()
        finally:
            self.owner.close()


def register_hub(registry: LegacyRegistry, *, store_backed: bool, settings: bool = False) -> None:
    """Declare the hub graph: the scope inputs, the legacy bridge's leaves, the store adapters and the adopted areas
    over them — or, store-free, each store-backed registration absent. The settings ports resolve from the borrowed
    settings graph when ``settings`` is set, and are absent otherwise, whether or not the graph is store-backed."""
    scopes.register(registry)
    documents.register(registry)
    platform_tracing_wiring.register(registry)
    events.register(registry)
    settings_wiring.register_boot(registry)
    forge.register_process(registry)
    if store_backed:
        delivery.register(registry)
        register_legacy(registry)
        secrets.register(registry)
        findings.register(registry, store.register_findings(registry))
        graph_area.register(registry, store.register_graphs(registry))
        _register_urgency(registry)
        garden_area.register(registry)
        cadence_area.register(registry)
        _register_config(registry)
        _register_projects(registry)
        config_apply.register(registry)
        live_config.register(registry)
        forge.register_tenant(registry)
        work_sources.register(registry)
        api.register(registry)
    else:
        register_legacy_absent(registry)
        secrets.register_absent(registry)
        store.register_findings_absent(registry)
        findings.register_absent(registry)
        store.register_graphs_absent(registry)
        graph_area.register_absent(registry)
        store.register_urgency_absent(registry)
        urgency_area.register_absent(registry)
        garden_area.register_absent(registry)
        cadence_area.register_absent(registry)
        store.register_config_absent(registry)
        config_wiring.register_absent(registry)
        store.register_projects_absent(registry)
        projects.register_absent(registry)
        config_apply.register_absent(registry)
        live_config.register_absent(registry)
        forge.register_tenant_absent(registry)
        work_sources.register_absent(registry)
        api.register_absent(registry)
    if store_backed and settings:
        settings_wiring.register(registry)
    else:
        settings_wiring.register_boot_source(registry)
        settings_wiring.register_absent(registry)


def _register_projects(registry: LegacyRegistry) -> None:
    """The project adapters, and the projects area's ports and services over them."""
    refs = store.register_projects(registry)
    projects.register(
        registry,
        projects=refs.projects,
        links=refs.links,
        positions=refs.positions,
        chunk_targets=refs.chunk_targets,
    )


def _register_urgency(registry: LegacyRegistry) -> None:
    """The urgency adapters, and the urgency area's ports and services over them."""
    refs = store.register_urgency(registry)
    urgency_area.register(
        registry, urgencies=refs.urgencies, order=refs.order, settings=refs.settings, chunks=refs.chunks
    )


def _register_config(registry: LegacyRegistry) -> None:
    """The configured-record adapters, and the config area's ports over them."""
    refs = store.register_config(registry)
    config_wiring.register(
        registry,
        work_sources=refs.work_sources,
        repositories=refs.repositories,
        forges=refs.forges,
        credentials=refs.credentials,
        secrets=refs.secrets,
        sealed=refs.secrets,
        secret_references=refs.secret_references,
        forge_references=refs.forge_references,
        credential_references=refs.credential_references,
        changes=refs.changes,
        apply_writer=refs.apply_writer,
        import_writer=refs.import_writer,
        revisions=refs.revisions,
    )


def process_values(
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> list[ScopeValue[Any]]:
    """The scope values every hub graph opens with."""
    return [
        supply(port(IClock), clock),
        supply(port(HubConfig), config),
        supply(port(IPlatformTracing), platform_tracing),
        supply(port(HubEnviron), HubEnviron(environ)),
        supply(port(HubActions), actions),
        supply(port(PackagedGraphs), graphs),
        supply(port(PackagedSystemArtifacts), system_artifacts),
    ]


def store_backed_values(
    connections: HubStoreConnections,
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    settings: HubSettingsGraph | None = None,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> ScopeValues:
    """The store-backed graph's scope values; ``settings`` is supplied only to a graph declaring the settings ports."""
    values: list[ScopeValue[Any]] = [
        supply(port(HubStoreConnections), connections),
        *process_values(
            clock=clock,
            config=config,
            platform_tracing=platform_tracing,
            environ=environ,
            actions=actions,
            graphs=graphs,
            system_artifacts=system_artifacts,
        ),
    ]
    if settings is not None:
        values.append(supply(port(HubSettingsGraph), settings))
    return _scoped(values)


def build_store_backed(
    registry: LegacyRegistry,
    connections: HubStoreConnections,
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    settings: HubSettingsGraph | None = None,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> HubProcess:
    """Open the store-backed graph ``registry`` declares over the borrowed ``connections`` and process inputs."""
    values = store_backed_values(
        connections,
        clock=clock,
        config=config,
        platform_tracing=platform_tracing,
        environ=environ,
        actions=actions,
        settings=settings,
        graphs=graphs,
        system_artifacts=system_artifacts,
    )
    return store_backed_process(build_container(registry, HUB_LIFETIMES, owner=HUB_OWNER, values=values))


def store_backed_process(owner: OwnedChild) -> HubProcess:
    """The store-backed :class:`HubProcess` over its opened container, which is closed if the tenant reads fail."""
    try:
        reads = owner.resolver.get(HubScopedReads)
    except BaseException:
        owner.close()
        raise
    return _process(owner, confirm_open=reads.tenants.confirm_open, open_tenants=reads.tenants.open_tenants)


def build_store_free(
    registry: LegacyRegistry,
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> HubProcess:
    """Open the store-free graph ``registry`` declares; it holds no tenant, so every tenant open is refused."""
    values = process_values(
        clock=clock,
        config=config,
        platform_tracing=platform_tracing,
        environ=environ,
        actions=actions,
        graphs=graphs,
        system_artifacts=system_artifacts,
    )
    owner = build_container(registry, HUB_LIFETIMES, owner=HUB_OWNER, values=_scoped(values))
    return _process(owner, confirm_open=_no_tenant, open_tenants=tuple)


def open_store_backed(
    connections: HubStoreConnections,
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    settings: HubSettingsGraph | None = None,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> HubProcess:
    """The hub's store-backed process, registered and opened; the settings ports are present only with ``settings``."""
    return build_store_backed(
        registered(store_backed=True, settings=settings is not None),
        connections,
        clock=clock,
        graphs=graphs,
        system_artifacts=system_artifacts,
        config=config,
        platform_tracing=platform_tracing,
        environ=environ,
        actions=actions,
        settings=settings,
    )


def open_store_free(
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> HubProcess:
    """The hub's store-free process, registered and opened."""
    return build_store_free(
        registered(store_backed=False),
        clock=clock,
        config=config,
        platform_tracing=platform_tracing,
        environ=environ,
        actions=actions,
        graphs=graphs,
        system_artifacts=system_artifacts,
    )


def registered(*, store_backed: bool, settings: bool = False) -> LegacyRegistry:
    """A fresh registry holding the hub graph :func:`register_hub` declares."""
    registry = LegacyRegistry()
    register_hub(registry, store_backed=store_backed, settings=settings)
    return registry


def _scoped(values: list[ScopeValue[Any]]) -> ScopeValues:
    return ScopeValues(tuple(values))


def _process(
    owner: OwnedChild, *, confirm_open: Callable[[TenantId], None], open_tenants: Callable[[], Iterable[TenantId]]
) -> HubProcess:
    return HubProcess(owner, TenantContainers(owner, confirm_open=confirm_open, open_tenants=open_tenants))


def _no_tenant(tenant: TenantId) -> None:
    raise UnknownTenant(tenant)
