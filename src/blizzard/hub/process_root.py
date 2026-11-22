"""The hub process's composition root: it registers the hub graph and opens the validated container over it.

Registration and building are two steps, so a stand-in root can reuse the hub's registration and add to it before it
builds. The store-backed graph declares the legacy bridge's leaves; the store-free graph declares each of them absent,
so a route that reaches one answers the store's unavailability rather than serving on nothing. A graph opened
without settings declares the settings ports absent the same way.

The process scope borrows every value it is opened with — the store's connections, the clock, the boot config, the
platform tracing handle, the process environment, the settings graph, the hub's actions and the packaged graph and
system-artifact loaders — and the :class:`HubProcess` it returns owns only the container: closing it closes every
tenant scope, then the process scope, and leaves the borrowed values to the entrypoint that built them."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.platform_tracing import wiring as platform_tracing_wiring
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.foundation.registry.interface import port
from blizzard.foundation.registry.internal.recording import Rewrites
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
from blizzard.hub.domain.config import wiring as config_wiring
from blizzard.hub.domain.config_apply import wiring as config_apply
from blizzard.hub.domain.garden.findings import wiring as findings
from blizzard.hub.domain.graph import wiring as graph_area
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
from blizzard.hub.store.internal.config_apply_store import ConfigApplyStore
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.config_import_store import ConfigImportStore
from blizzard.hub.store.internal.config_revisions_store import ConfigRevisionsStore
from blizzard.hub.store.internal.credential_record_store import CredentialRecordStore
from blizzard.hub.store.internal.credential_referrers import CredentialReferrersStore
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.forge_referrers import ForgeReferrersStore
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.secret_referrers import SecretReferrersStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
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
        _register_config(registry)
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
        store.register_config_absent(registry)
        config_wiring.register_absent(registry)
        config_apply.register_absent(registry)
        live_config.register_absent(registry)
        forge.register_tenant_absent(registry)
        work_sources.register_absent(registry)
        api.register_absent(registry)
    if store_backed and settings:
        settings_wiring.register(registry)
    else:
        settings_wiring.register_absent(registry)


def _register_config(registry: LegacyRegistry) -> None:
    """The configured-record adapters, and the config area's ports over them."""
    refs = store.register_config(registry)
    config_wiring.register(
        registry,
        work_sources=refs.of(WorkSourceRecordStore),
        repositories=refs.of(RepositoryRecordStore),
        forges=refs.of(ForgeRecordStore),
        credentials=refs.of(CredentialRecordStore),
        secrets=refs.of(SecretStore),
        sealed=refs.of(SecretStore),
        secret_references=refs.of(SecretReferrersStore),
        forge_references=refs.of(ForgeReferrersStore),
        credential_references=refs.of(CredentialReferrersStore),
        changes=refs.of(ConfigChangeStore),
        apply_writer=refs.of(ConfigApplyStore),
        import_writer=refs.of(ConfigImportStore),
        revisions=refs.of(ConfigRevisionsStore),
    )


def _inputs(
    config: HubConfig | None,
    platform_tracing: IPlatformTracing | None,
    environ: Mapping[str, str] | None,
    actions: HubActions | None = None,
) -> list[ScopeValue[Any]]:
    """The scope values of the process inputs supplied; a graph opened without one serves nothing that reads it."""
    values: list[ScopeValue[Any]] = []
    if actions is not None:
        values.append(supply(port(HubActions), actions))
    if config is not None:
        values.append(supply(port(HubConfig), config))
    if platform_tracing is not None:
        values.append(supply(port(IPlatformTracing), platform_tracing))
    if environ is not None:
        values.append(supply(port(HubEnviron), HubEnviron(environ)))
    return values


#: A test root's replacements over the registered graph, built from the registry before the container is.
type Overrides = Callable[[LegacyRegistry], Rewrites]


def build_store_backed(
    registry: LegacyRegistry,
    connections: HubStoreConnections,
    *,
    clock: IClock,
    actions: HubActions,
    settings: HubSettingsGraph | None = None,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
    config: HubConfig | None = None,
    platform_tracing: IPlatformTracing | None = None,
    environ: Mapping[str, str] | None = None,
    overrides: Overrides | None = None,
) -> HubProcess:
    """Open the store-backed graph ``registry`` declares over the borrowed ``connections``, ``clock``, ``actions``, the
    packaged ``graphs`` and ``system_artifacts``, boot config, tracing handle, environment and, when the graph declares
    the settings ports, ``settings``. ``overrides`` is a test root's replacements."""
    values: list[ScopeValue[Any]] = [
        supply(port(HubStoreConnections), connections),
        supply(port(IClock), clock),
        *_packaged(graphs, system_artifacts),
        *_inputs(config, platform_tracing, environ, actions),
    ]
    if settings is not None:
        values.append(supply(port(HubSettingsGraph), settings))
    owner = _opened(registry, *values, overrides=overrides(registry) if overrides is not None else None)
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
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
    config: HubConfig | None = None,
    platform_tracing: IPlatformTracing | None = None,
) -> HubProcess:
    """Open the store-free graph ``registry`` declares; it holds no tenant, so every tenant open is refused."""
    return _process(
        _opened(
            registry,
            supply(port(IClock), clock),
            *_packaged(graphs, system_artifacts),
            *_inputs(config, platform_tracing, None),
        ),
        confirm_open=_no_tenant,
        open_tenants=tuple,
    )


def open_store_backed(
    connections: HubStoreConnections,
    *,
    clock: IClock,
    actions: HubActions,
    settings: HubSettingsGraph | None = None,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
    config: HubConfig | None = None,
    platform_tracing: IPlatformTracing | None = None,
    environ: Mapping[str, str] | None = None,
    overrides: Overrides | None = None,
) -> HubProcess:
    """The hub's store-backed process, registered and opened; the settings ports are present only with ``settings``."""
    registry = _registered(store_backed=True, settings=settings is not None)
    return build_store_backed(
        registry,
        connections,
        clock=clock,
        actions=actions,
        settings=settings,
        graphs=graphs,
        system_artifacts=system_artifacts,
        config=config,
        platform_tracing=platform_tracing,
        environ=environ,
        overrides=overrides,
    )


def open_store_free(
    *,
    clock: IClock,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
    config: HubConfig | None = None,
    platform_tracing: IPlatformTracing | None = None,
) -> HubProcess:
    """The hub's store-free process, registered and opened."""
    return build_store_free(
        _registered(store_backed=False),
        clock=clock,
        graphs=graphs,
        system_artifacts=system_artifacts,
        config=config,
        platform_tracing=platform_tracing,
    )


def _registered(*, store_backed: bool, settings: bool = False) -> LegacyRegistry:
    registry = LegacyRegistry()
    register_hub(registry, store_backed=store_backed, settings=settings)
    return registry


def _packaged(graphs: PackagedGraphs, system_artifacts: PackagedSystemArtifacts) -> tuple[ScopeValue[Any], ...]:
    return (supply(port(PackagedGraphs), graphs), supply(port(PackagedSystemArtifacts), system_artifacts))


def _opened(registry: LegacyRegistry, *values: ScopeValue[Any], overrides: Rewrites | None = None) -> OwnedChild:
    return build_container(registry, HUB_LIFETIMES, owner=HUB_OWNER, values=ScopeValues(values), overrides=overrides)


def _process(
    owner: OwnedChild, *, confirm_open: Callable[[TenantId], None], open_tenants: Callable[[], Iterable[TenantId]]
) -> HubProcess:
    return HubProcess(owner, TenantContainers(owner, confirm_open=confirm_open, open_tenants=open_tenants))


def _no_tenant(tenant: TenantId) -> None:
    raise UnknownTenant(tenant)
