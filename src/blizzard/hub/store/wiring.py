"""The hub store's adapter registrations: every adapter and transaction primitive ``hub/store`` defines.

The tenant's adapters are registered at the tenant scope, each over the tenant's one :class:`ScopedConnections`. Each
registration is a service, except the seam itself (opened only through the borrowed connections' ``scoped``) and the
adapters a borrowed-connection root also builds, whose one constructor function the registry shares. The registrations
hand back the ``Ref`` an adopted area or the legacy bridge aliases its own ports onto, so a port and its adapter are
one instance. The store-free graph declares each registered key absent, from the same roster, so the two cannot drift.

This is the only module besides the composition roots that names ``hub/store/internal``. It acquires nothing: the
engine and the connections are borrowed process values the entrypoint supplies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.foundation.roles import adapter_model
from blizzard.hub.auth.invitations import IInvitationTokenReads
from blizzard.hub.auth.service import InvitationWriters
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.observability.egress.repository import IReadUsageCorpus
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.analytics_event_query_store import AnalyticsEventQueryStore
from blizzard.hub.store.internal.analytics_operational_store import AnalyticsOperationalStore
from blizzard.hub.store.internal.cadence_store import CadenceStore
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.chunk_targets_store import ChunkTargetsStore
from blizzard.hub.store.internal.config_apply_store import ConfigApplyStore
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.config_import_store import ConfigImportStore
from blizzard.hub.store.internal.config_revisions_store import ConfigRevisionsStore
from blizzard.hub.store.internal.credential_record_store import CredentialRecordStore
from blizzard.hub.store.internal.credential_referrers import CredentialReferrersStore
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.env_timeline_store import EnvTimelineStore
from blizzard.hub.store.internal.errand_runs_store import ErrandRunsStore
from blizzard.hub.store.internal.finding_store import FindingSetStore, FindingStore
from blizzard.hub.store.internal.flow_store import FlowStore, tenant_flows
from blizzard.hub.store.internal.forge_annotation_store import ForgeAnnotationStore
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.forge_referrers import ForgeReferrersStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.garden_proposal_closure_store import GardenProposalClosureStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.garden_run_store import GardenRunStore
from blizzard.hub.store.internal.garden_sweeps_store import GardenSweepsStore
from blizzard.hub.store.internal.garden_trend_store import GardenTrendStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.hub_event_log_store import HubEventLogStore
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads, build_hub_scoped_reads
from blizzard.hub.store.internal.invitation_store import InvitationStore
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.project_position_store import ProjectPositionStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.review_findings_store import ReviewFindingsStore
from blizzard.hub.store.internal.routine_scope_store import RoutineScopeStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.internal.secret_referrers import SecretReferrersStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.tenant_admin_store import TenantAdministrationStore
from blizzard.hub.store.internal.tenant_settings_store import TenantSettingsStore
from blizzard.hub.store.internal.tenant_teardown_store import TenantTeardownStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.store.internal.urgency_chunks_store import UrgencyChunksStore
from blizzard.hub.store.internal.urgency_store import UrgencyOrderStore, UrgencyStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import ClosedTenantUnits, HubTransactions
from blizzard.hub.tenancy import STORE_UNAVAILABLE, StoreScope

#: Every other tenant adapter built as a service over the tenant's connections (one also takes the link port).
_TENANT_SERVICES: tuple[type[Any], ...] = (
    TranscriptSegmentStore,
    TranscriptEventStore,
    AnalyticsEventQueryStore,
    AnalyticsOperationalStore,
    ErrandRunsStore,
    GardenDeliveryStore,
    ReviewFindingsStore,
    GardenTrendStore,
    GardenSweepsStore,
    GardenRunStore,
    EnvTimelineStore,
    ForgeAnnotationStore,
)


class StoreRefs:
    """The ``Ref`` of each adapter a registration handed back, by its class — what an adopted area or the legacy
    bridge aliases its own ports onto."""

    def __init__(self, refs: Mapping[type[Any], Ref[Any]]) -> None:
        self._refs = dict(refs)

    def of[T](self, adapter: type[T]) -> Ref[T]:
        """The ``Ref`` of ``adapter``'s one registration."""
        return self._refs[adapter]


#: The tenant services a legacy-bridge alias targets, registered apart so their ``Ref`` is handed back.
_TENANT_ALIASED: tuple[type[Any], ...] = (
    HubTransactions,
    CadenceStore,
    InvitationStore,
)

#: The garden authoring adapters, whose ``Ref`` the garden area aliases its ports onto.
_GARDEN_AUTHORING_SERVICES: tuple[type[Any], ...] = (
    ScopeStore,
    RoutineStore,
    RoutineScopeStore,
    GardenProposalStore,
    GardenProposalClosureStore,
    FindingSetStore,
    RunContextStore,
)

#: The configured-record adapters, whose ``Ref`` the config area aliases its ports onto.
_CONFIG_SERVICES: tuple[type[Any], ...] = (
    SecretStore,
    WorkSourceRecordStore,
    RepositoryRecordStore,
    ForgeRecordStore,
    CredentialRecordStore,
    SecretReferrersStore,
    ForgeReferrersStore,
    CredentialReferrersStore,
    ConfigChangeStore,
    ConfigApplyStore,
    ConfigImportStore,
    ConfigRevisionsStore,
)

#: The tenant keys registered as factories rather than services.
_TENANT_FACTORY_KEYS: tuple[type[Any], ...] = (ScopedConnections,)


def scoped_connections(connections: HubStoreConnections, scope: StoreScope) -> ScopedConnections:
    """The tenant's scoped connections: the one seam a tenant adapter reads and writes through."""
    return connections.scoped(scope)


def register_tenant(registry: Registry) -> StoreRefs:
    """Declare every tenant adapter but the configured records' and the chunk operations' once per tenant, handing
    back the ``Ref`` of each adapter the legacy bridge's ports alias."""
    registry.factory(scoped_connections, lifetime=Lifetime.TENANT)
    aliased = StoreRefs({each: registry.service(each, lifetime=Lifetime.TENANT) for each in _TENANT_ALIASED})
    for each in _TENANT_SERVICES:
        registry.service(each, lifetime=Lifetime.TENANT)
    return aliased


def register_tenant_absent(registry: Registry) -> None:
    """Declare every tenant adapter absent, for the store-free graph."""
    for missing in (*_TENANT_FACTORY_KEYS, *_TENANT_SERVICES, *_TENANT_ALIASED):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


@adapter_model
@dataclass(frozen=True)
class ChunkOperationsRefs:
    """The ``Ref`` of each adapter the chunk operations' areas alias their ports onto."""

    chunks: Ref[ChunkStores]
    work_items: Ref[WorkItemStore]
    flows: Ref[FlowStore]


def register_chunk_operations(registry: Registry) -> ChunkOperationsRefs:
    """Declare the tenant's chunk-store bundle, hub work-item store and flow store once per tenant, handing back the
    ``Ref`` of each, which the chunk, operations and work-items areas' ports alias."""
    return ChunkOperationsRefs(
        chunks=registry.factory(build_chunk_stores, lifetime=Lifetime.TENANT),
        work_items=registry.service(WorkItemStore, lifetime=Lifetime.TENANT),
        flows=registry.factory(tenant_flows, lifetime=Lifetime.TENANT),
    )


def register_chunk_operations_absent(registry: Registry) -> None:
    """Declare the chunk operations' adapters absent, for the store-free graph."""
    for missing in (ChunkStores, WorkItemStore, FlowStore):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


@adapter_model
@dataclass(frozen=True)
class GardenAuthoringRefs:
    """The ``Ref`` of each garden authoring adapter, named for the garden area's port family it fills."""

    scopes: Ref[ScopeStore]
    routines: Ref[RoutineStore]
    routine_scopes: Ref[RoutineScopeStore]
    proposals: Ref[GardenProposalStore]
    closures: Ref[GardenProposalClosureStore]
    finding_sets: Ref[FindingSetStore]
    run_contexts: Ref[RunContextStore]


def register_garden_authoring(registry: Registry) -> GardenAuthoringRefs:
    """Declare the tenant's garden authoring adapters, handing back the ``Ref`` of each, which the garden area's ports
    alias."""
    refs = StoreRefs({each: registry.service(each, lifetime=Lifetime.TENANT) for each in _GARDEN_AUTHORING_SERVICES})
    return GardenAuthoringRefs(
        scopes=refs.of(ScopeStore),
        routines=refs.of(RoutineStore),
        routine_scopes=refs.of(RoutineScopeStore),
        proposals=refs.of(GardenProposalStore),
        closures=refs.of(GardenProposalClosureStore),
        finding_sets=refs.of(FindingSetStore),
        run_contexts=refs.of(RunContextStore),
    )


def register_garden_authoring_absent(registry: Registry) -> None:
    """Declare the tenant's garden authoring adapters absent, for the store-free graph."""
    for missing in _GARDEN_AUTHORING_SERVICES:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


#: The adapters built once per hub, as services over the borrowed connections.
_PROCESS_SERVICES: tuple[type[Any], ...] = (
    TenantAdministrationStore,
    TenantTeardownStore,
    EgressStore,
    ClosedTenantUnits,
)

#: Every process key registered, for the store-free graph to declare absent.
_PROCESS_KEYS: tuple[type[Any], ...] = (
    HubScopedReads,
    IReadUsageCorpus,
    IInvitationTokenReads,
    HubEventLogStore,
    *_PROCESS_SERVICES,
)


def usage_corpus(reads: HubScopedReads) -> IReadUsageCorpus:
    """The hub-scoped reads' usage corpus, as its port."""
    return reads.corpus


def invitation_token_reads(reads: HubScopedReads) -> IInvitationTokenReads:
    """The hub-scoped reads' invitation-token reads, as their port."""
    return reads.invitations


def invitation_writers() -> InvitationWriters:
    """Opens a tenant's invitation writer: the invitation store, over the tenant's connections."""
    return InvitationStore


def register_process(registry: Registry) -> Ref[HubEventLogStore]:
    """Declare every hub-level adapter once per process, handing back the hub event log's ``Ref``, which the legacy
    bridge's ``IReadHubEventLog`` aliases."""
    registry.factory(build_hub_scoped_reads, lifetime=Lifetime.PROCESS)
    registry.factory(usage_corpus, lifetime=Lifetime.PROCESS)
    registry.factory(invitation_token_reads, lifetime=Lifetime.PROCESS)
    registry.factory(invitation_writers, lifetime=Lifetime.PROCESS)
    hub_events = registry.service(HubEventLogStore, lifetime=Lifetime.PROCESS)
    for each in _PROCESS_SERVICES:
        registry.service(each, lifetime=Lifetime.PROCESS)
    return hub_events


def register_process_absent(registry: Registry) -> None:
    """Declare every hub-level adapter absent, for the store-free graph."""
    for missing in _PROCESS_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    # A callable alias keys the opener, so it is named as its key rather than as a class.
    registry.absent(port(cast("type[Any]", InvitationWriters)), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)


@adapter_model
@dataclass(frozen=True)
class ConfigRefs:
    """The ``Ref`` of each configured-record adapter, named for the config area's port it fills."""

    work_sources: Ref[WorkSourceRecordStore]
    repositories: Ref[RepositoryRecordStore]
    forges: Ref[ForgeRecordStore]
    credentials: Ref[CredentialRecordStore]
    secrets: Ref[SecretStore]
    secret_references: Ref[SecretReferrersStore]
    forge_references: Ref[ForgeReferrersStore]
    credential_references: Ref[CredentialReferrersStore]
    changes: Ref[ConfigChangeStore]
    apply_writer: Ref[ConfigApplyStore]
    import_writer: Ref[ConfigImportStore]
    revisions: Ref[ConfigRevisionsStore]


def register_config(registry: Registry) -> ConfigRefs:
    """Declare the tenant's configured-record adapters, handing back the ``Ref`` of each, which the config area's ports
    alias."""
    refs = StoreRefs({each: registry.service(each, lifetime=Lifetime.TENANT) for each in _CONFIG_SERVICES})
    return ConfigRefs(
        work_sources=refs.of(WorkSourceRecordStore),
        repositories=refs.of(RepositoryRecordStore),
        forges=refs.of(ForgeRecordStore),
        credentials=refs.of(CredentialRecordStore),
        secrets=refs.of(SecretStore),
        secret_references=refs.of(SecretReferrersStore),
        forge_references=refs.of(ForgeReferrersStore),
        credential_references=refs.of(CredentialReferrersStore),
        changes=refs.of(ConfigChangeStore),
        apply_writer=refs.of(ConfigApplyStore),
        import_writer=refs.of(ConfigImportStore),
        revisions=refs.of(ConfigRevisionsStore),
    )


def register_config_absent(registry: Registry) -> None:
    """Declare the tenant's configured-record adapters absent, for the store-free graph."""
    for missing in _CONFIG_SERVICES:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


def register_findings(registry: Registry) -> Ref[FindingStore]:
    """Declare the tenant's finding adapter."""
    return registry.service(FindingStore, lifetime=Lifetime.TENANT)


def register_findings_absent(registry: Registry) -> None:
    """Declare the tenant's finding adapter absent, for the store-free graph."""
    registry.absent(port(FindingStore), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


def register_graphs(registry: Registry) -> Ref[GraphStore]:
    """Declare the tenant's graph adapter."""
    return registry.service(GraphStore, lifetime=Lifetime.TENANT)


def register_graphs_absent(registry: Registry) -> None:
    """Declare the tenant's graph adapter absent, for the store-free graph."""
    registry.absent(port(GraphStore), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


#: The project-area adapters, whose ``Ref`` the projects area aliases its ports onto.
_PROJECT_SERVICES: tuple[type[Any], ...] = (ProjectStore, ProjectLinkStore, ProjectPositionStore, ChunkTargetsStore)


@adapter_model
@dataclass(frozen=True)
class ProjectRefs:
    """The ``Ref`` of each project-area adapter, named for the projects area's port it fills."""

    projects: Ref[ProjectStore]
    links: Ref[ProjectLinkStore]
    positions: Ref[ProjectPositionStore]
    chunk_targets: Ref[ChunkTargetsStore]


def register_projects(registry: Registry) -> ProjectRefs:
    """Declare the tenant's project adapters, handing back the ``Ref`` of each, which the area's ports alias."""
    refs = StoreRefs({each: registry.service(each, lifetime=Lifetime.TENANT) for each in _PROJECT_SERVICES})
    return ProjectRefs(
        projects=refs.of(ProjectStore),
        links=refs.of(ProjectLinkStore),
        positions=refs.of(ProjectPositionStore),
        chunk_targets=refs.of(ChunkTargetsStore),
    )


def register_projects_absent(registry: Registry) -> None:
    """Declare the tenant's project adapters absent, for the store-free graph."""
    for missing in _PROJECT_SERVICES:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


#: The urgency adapters the urgency area aliases its ports onto.
_URGENCY_SERVICES: tuple[type[Any], ...] = (UrgencyStore, UrgencyOrderStore, TenantSettingsStore, UrgencyChunksStore)


@adapter_model
@dataclass(frozen=True)
class UrgencyRefs:
    """The ``Ref`` of each urgency adapter, named for the port family the urgency area aliases onto it."""

    urgencies: Ref[UrgencyStore]
    order: Ref[UrgencyOrderStore]
    settings: Ref[TenantSettingsStore]
    chunks: Ref[UrgencyChunksStore]


def register_urgency(registry: Registry) -> UrgencyRefs:
    """Declare the tenant's urgency adapters, handing back the ``Ref`` of each, which the urgency area's ports alias."""
    refs = StoreRefs({each: registry.service(each, lifetime=Lifetime.TENANT) for each in _URGENCY_SERVICES})
    return UrgencyRefs(
        urgencies=refs.of(UrgencyStore),
        order=refs.of(UrgencyOrderStore),
        settings=refs.of(TenantSettingsStore),
        chunks=refs.of(UrgencyChunksStore),
    )


def register_urgency_absent(registry: Registry) -> None:
    """Declare the tenant's urgency adapters absent, for the store-free graph."""
    for missing in _URGENCY_SERVICES:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


#: The fleet registry adapters, whose ``Ref`` the runners area aliases its ports onto.
_FLEET_SERVICES: tuple[type[Any], ...] = (RunnerRegistryStore, HostRegistryStore)


@adapter_model
@dataclass(frozen=True)
class FleetRefs:
    """The ``Ref`` of each fleet registry adapter, named for the runners area's port family it fills."""

    runners: Ref[RunnerRegistryStore]
    hosts: Ref[HostRegistryStore]


def register_fleet(registry: Registry) -> FleetRefs:
    """Declare the tenant's runner and host registry adapters, handing back the ``Ref`` of each, which the runners
    area's ports alias."""
    refs = StoreRefs({each: registry.service(each, lifetime=Lifetime.TENANT) for each in _FLEET_SERVICES})
    return FleetRefs(runners=refs.of(RunnerRegistryStore), hosts=refs.of(HostRegistryStore))


def register_fleet_absent(registry: Registry) -> None:
    """Declare the tenant's fleet registry adapters absent, for the store-free graph."""
    for missing in _FLEET_SERVICES:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
