"""The hub store's adapter registrations: every adapter and transaction primitive ``hub/store`` defines.

The tenant's adapters are registered at the tenant scope, each over the tenant's one :class:`ScopedConnections`. Each
registration is a service, except the seam itself (opened only through the borrowed connections' ``scoped``) and the
adapters a borrowed-connection root also builds, whose one constructor function the registry shares. The registrations
hand back the ``Ref`` an adopted area or the legacy bridge aliases its own ports onto, so a port and its adapter are
one instance. The store-free graph declares each registered key absent, from the same roster, so the two cannot drift.

This is the only module besides the composition roots that names ``hub/store/internal``. It acquires nothing: the
engine and the connections are borrowed process values the entrypoint supplies."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
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
from blizzard.hub.store.internal.tenant_settings_store import TenantSettingsStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.store.internal.urgency_chunks_store import UrgencyChunksStore
from blizzard.hub.store.internal.urgency_store import UrgencyOrderStore, UrgencyStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import HubTransactions
from blizzard.hub.tenancy import STORE_UNAVAILABLE, StoreScope

#: Every other tenant adapter built as a service over the tenant's connections (one also takes the link port).
_TENANT_SERVICES: tuple[type[Any], ...] = (
    RunnerRegistryStore,
    HostRegistryStore,
    WorkItemStore,
    GardenProposalStore,
    GardenProposalClosureStore,
    SecretStore,
    WorkSourceRecordStore,
    RepositoryRecordStore,
    ForgeRecordStore,
    CredentialRecordStore,
    SecretReferrersStore,
    ForgeReferrersStore,
    CredentialReferrersStore,
    ConfigChangeStore,
    ChunkTargetsStore,
    ProjectPositionStore,
    ConfigApplyStore,
    ConfigImportStore,
    InvitationStore,
    UrgencyStore,
    UrgencyOrderStore,
    TenantSettingsStore,
    UrgencyChunksStore,
    ConfigRevisionsStore,
    GraphStore,
    TranscriptSegmentStore,
    TranscriptEventStore,
    AnalyticsEventQueryStore,
    AnalyticsOperationalStore,
    ScopeStore,
    RoutineStore,
    RoutineScopeStore,
    CadenceStore,
    ErrandRunsStore,
    FindingSetStore,
    RunContextStore,
    GardenDeliveryStore,
    ReviewFindingsStore,
    GardenTrendStore,
    GardenSweepsStore,
    GardenRunStore,
    EnvTimelineStore,
    ForgeAnnotationStore,
)

#: The tenant services a transitional alias targets, registered apart so their ``Ref`` is handed back.
_TENANT_ALIASED: tuple[type[Any], ...] = (HubTransactions, ProjectStore, ProjectLinkStore)

#: The tenant keys registered as factories rather than services.
_TENANT_FACTORY_KEYS: tuple[type[Any], ...] = (ScopedConnections, ChunkStores, FlowStore)


def scoped_connections(connections: HubStoreConnections, scope: StoreScope) -> ScopedConnections:
    """The tenant's scoped connections: the one seam a tenant adapter reads and writes through."""
    return connections.scoped(scope)


def register_tenant(registry: Registry) -> tuple[Ref[HubTransactions], Ref[ProjectStore], Ref[ProjectLinkStore]]:
    """Declare every tenant adapter once per tenant, handing back the ``Ref`` of the transactions, the projects and the
    links, which the legacy bridge's ports alias."""
    registry.factory(scoped_connections, lifetime=Lifetime.TENANT)
    registry.factory(build_chunk_stores, lifetime=Lifetime.TENANT)
    registry.factory(tenant_flows, lifetime=Lifetime.TENANT)
    transactions = registry.service(HubTransactions, lifetime=Lifetime.TENANT)
    projects = registry.service(ProjectStore, lifetime=Lifetime.TENANT)
    project_links = registry.service(ProjectLinkStore, lifetime=Lifetime.TENANT)
    for each in _TENANT_SERVICES:
        registry.service(each, lifetime=Lifetime.TENANT)
    return transactions, projects, project_links


def register_tenant_absent(registry: Registry) -> None:
    """Declare every tenant adapter absent, for the store-free graph."""
    for missing in (*_TENANT_FACTORY_KEYS, *_TENANT_SERVICES, *_TENANT_ALIASED):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


def register_findings(registry: Registry) -> Ref[FindingStore]:
    """Declare the tenant's finding adapter."""
    return registry.service(FindingStore, lifetime=Lifetime.TENANT)


def register_findings_absent(registry: Registry) -> None:
    """Declare the tenant's finding adapter absent, for the store-free graph."""
    registry.absent(port(FindingStore), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
