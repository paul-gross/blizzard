"""The legacy bags' retained fields are the container's instances, and every port of an adapter is one object.

While an area migrates, a bag field the legacy builder still fills must be the very instance the tenant's container
scope holds, never a second copy; and each port an adapter is registered under resolves to that adapter's instance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from blizzard.hub.composition import HubCore, TenantServices, TenantStores
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.documents.codec import IConfigCodec
from blizzard.hub.domain.cadence.ports import IReadCadenceRepository, IReadErrandRunsRepository, IWriteCadenceRepository
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.ingest import IWorkItemProjectRead
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository, IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.decisions import IReadChunkDecisionsRepository, IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.delivery import IReadChunkDeliveryRepository, IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.dependencies import (
    IReadChunkDependenciesRepository,
    IWriteChunkDependenciesRepository,
)
from blizzard.hub.domain.chunk.ports.escalations import (
    IReadChunkEscalationsRepository,
    IWriteChunkEscalationsRepository,
)
from blizzard.hub.domain.chunk.ports.events import IReadChunkEventsRepository, IWriteChunkEventsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.flow_submissions import IReadChunkFlowSubmissions
from blizzard.hub.domain.chunk.ports.hub_exec import IReadChunkHubExecRepository, IWriteChunkHubExecRepository
from blizzard.hub.domain.chunk.ports.lifecycle import IReadChunkLifecycleRepository, IWriteChunkLifecycleRepository
from blizzard.hub.domain.chunk.ports.movement import IReadChunkMovementRepository, IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.questions import IReadChunkQuestionsRepository, IWriteChunkQuestionsRepository
from blizzard.hub.domain.chunk.ports.queue import IReadChunkQueueRepository, IWriteChunkQueueRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository, IWriteChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository, IWriteChunkRouteRepository
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.chunk.ports.usage import IReadChunkUsageRepository, IWriteChunkUsageRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository, IWriteChunkWorkRefsRepository
from blizzard.hub.domain.delivery.ports.flows import IReadFlows, IReadFlowViews, IWriteFlows
from blizzard.hub.domain.garden.delivery.materialize import (
    IReadGardenDeliveryRepository,
    IWriteGardenDeliveryRepository,
)
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import IReadFindingRepository, IWriteFindingRepository
from blizzard.hub.domain.garden.review.materialize import IReadReviewFindingsRepository, IWriteReviewFindingsRepository
from blizzard.hub.domain.graph.actions import HubActionCatalogue
from blizzard.hub.domain.graph.authoring import DefaultGraphService, GraphMintService
from blizzard.hub.domain.graph.model import (
    IReadGraphBodies,
    IReadGraphRepository,
    IReadNodeExecutors,
    IWriteGraphRepository,
)
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.observability.analytics.events import IReadTranscriptEvents, IWriteTranscriptEvents
from blizzard.hub.domain.observability.analytics.operational import IReadOperationalAnalytics
from blizzard.hub.domain.observability.analytics.queries import IReadAnalyticsEventQueries
from blizzard.hub.domain.observability.egress.repository import IReadEgress, IWriteEgressCursor
from blizzard.hub.domain.observability.env_timeline import IReadEnvTimelineFacts
from blizzard.hub.domain.observability.forge_status import IReadAnnotatedSources, IWriteAnnotatedSources
from blizzard.hub.domain.observability.hub_events import IReadHubEventLog, IWriteHubEventLog
from blizzard.hub.domain.observability.invariants import IHubInvariantChecker
from blizzard.hub.domain.observability.tracing.repository import (
    IReadProjectLabels,
    IReadTraceStatus,
    IReadTraceSteps,
    IWriteTraceCursor,
)
from blizzard.hub.domain.observability.transcripts import (
    IReadTranscriptSegments,
    IWriteTranscriptSegments,
)
from blizzard.hub.domain.operations.delete import IHubItemWithdrawals
from blizzard.hub.domain.projects.bulk_resolve import IBulkProjectLookup
from blizzard.hub.domain.projects.links import IReadProjectLinks, IWriteProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository, IWriteProjectRepository
from blizzard.hub.domain.projects.order import IReadProjectPositions, IWriteProjectPositions
from blizzard.hub.domain.projects.targets import ChunkTargets, IReadChunkTargets, IWriteChunkTargets
from blizzard.hub.domain.runners.host import IReadHostRegistry, IWriteHostRegistry
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, IWriteRunnerRegistry
from blizzard.hub.domain.urgency.ports import (
    IReadTenantSettings,
    IReadUrgencyChunks,
    IReadUrgencyOrder,
    IReadUrgencyRepository,
    IWriteTenantSettings,
    IWriteUrgencyChunks,
    IWriteUrgencyOrder,
    IWriteUrgencyRepository,
)
from blizzard.hub.domain.urgency.services import UrgencyReading
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.domain.work_items.ports import IReadWorkItemRepository, IWriteWorkItemRepository
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.graphs import PackagedGraphs
from blizzard.hub.store.internal.analytics_event_query_store import AnalyticsEventQueryStore
from blizzard.hub.store.internal.analytics_operational_store import AnalyticsOperationalStore
from blizzard.hub.store.internal.cadence_store import CadenceStore
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
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.internal.forge_annotation_store import ForgeAnnotationStore
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.forge_referrers import ForgeReferrersStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.garden_proposal_closure_store import GardenProposalClosureStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.hub_event_log_store import HubEventLogStore
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.store.internal.invariants import HubInvariants
from blizzard.hub.store.internal.invitation_store import InvitationStore
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.project_position_store import ProjectPositionStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.review_findings_store import ReviewFindingsStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.secret_referrers import SecretReferrersStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.tenant_settings_store import TenantSettingsStore
from blizzard.hub.store.internal.trace_store import ProjectLabels, TraceStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.store.internal.urgency_chunks_store import UrgencyChunksStore
from blizzard.hub.store.internal.urgency_store import UrgencyOrderStore, UrgencyStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import HubTransactions
from blizzard.hub.system_artifacts import PackagedSystemArtifacts

#: Each adapter the tenant scope owns, and the ports it is registered under.
ADAPTER_PORTS: Mapping[type[Any], tuple[type[Any], ...]] = {
    ScopedConnections: (),
    HubTransactions: (IHubTransactions,),
    ProjectStore: (IReadProjectRepository, IWriteProjectRepository, IBulkProjectLookup),
    ProjectLinkStore: (IReadProjectLinks, IWriteProjectLinks),
    RunnerRegistryStore: (IReadRunnerRegistry, IWriteRunnerRegistry),
    HostRegistryStore: (IReadHostRegistry, IWriteHostRegistry),
    WorkItemStore: (IReadWorkItemRepository, IWriteWorkItemRepository, IHubItemWithdrawals, IWorkItemProjectRead),
    CadenceStore: (IReadCadenceRepository, IWriteCadenceRepository),
    ErrandRunsStore: (IReadErrandRunsRepository,),
    ChunkStores: (),
    GardenProposalStore: (),
    GardenDeliveryStore: (IReadGardenDeliveryRepository, IWriteGardenDeliveryRepository),
    ReviewFindingsStore: (IReadReviewFindingsRepository, IWriteReviewFindingsRepository),
    GardenProposalClosureStore: (),
    SecretStore: (),
    WorkSourceRecordStore: (),
    RepositoryRecordStore: (),
    ForgeRecordStore: (),
    CredentialRecordStore: (),
    FlowStore: (IReadChunkFlowSubmissions, IReadFlows, IReadFlowViews, IWriteFlows),
    SecretReferrersStore: (),
    ForgeReferrersStore: (),
    CredentialReferrersStore: (),
    ConfigChangeStore: (),
    ChunkTargetsStore: (IReadChunkTargets, IWriteChunkTargets),
    ProjectPositionStore: (IReadProjectPositions, IWriteProjectPositions),
    ConfigApplyStore: (),
    ConfigImportStore: (),
    InvitationStore: (),
    UrgencyStore: (IReadUrgencyRepository, IWriteUrgencyRepository),
    UrgencyOrderStore: (IReadUrgencyOrder, IWriteUrgencyOrder),
    TenantSettingsStore: (IReadTenantSettings, IWriteTenantSettings),
    UrgencyChunksStore: (IReadUrgencyChunks, IWriteUrgencyChunks),
    ConfigRevisionsStore: (),
    FindingStore: (IReadFindingRepository, IWriteFindingRepository),
    GraphStore: (IReadGraphRepository, IWriteGraphRepository, IReadGraphBodies, IReadNodeExecutors),
    TranscriptSegmentStore: (IReadTranscriptSegments, IWriteTranscriptSegments),
    TranscriptEventStore: (IReadTranscriptEvents, IWriteTranscriptEvents),
    AnalyticsEventQueryStore: (IReadAnalyticsEventQueries,),
    AnalyticsOperationalStore: (IReadOperationalAnalytics,),
    EnvTimelineStore: (IReadEnvTimelineFacts,),
    ForgeAnnotationStore: (IReadAnnotatedSources, IWriteAnnotatedSources),
    ProjectLabels: (IReadProjectLabels,),
}


#: Each field of the tenant's one chunk-store bundle, and the chunk ports that resolve to it.
CHUNK_PORTS: Mapping[str, tuple[type[Any], ...]] = {
    "facts": (IReadChunkFactsRepository,),
    "record": (IReadChunkRecordRepository, IWriteChunkRecordRepository),
    "lifecycle": (IReadChunkLifecycleRepository, IWriteChunkLifecycleRepository),
    "work_refs": (IReadChunkWorkRefsRepository, IWriteChunkWorkRefsRepository),
    "queue": (IReadChunkQueueRepository, IWriteChunkQueueRepository),
    "route": (IReadChunkRouteRepository, IWriteChunkRouteRepository),
    "movement": (IReadChunkMovementRepository, IWriteChunkMovementRepository),
    "artifacts": (IReadChunkArtifactsRepository, IWriteChunkArtifactsRepository),
    "questions": (IReadChunkQuestionsRepository, IWriteChunkQuestionsRepository),
    "decisions": (IReadChunkDecisionsRepository, IWriteChunkDecisionsRepository),
    "escalations": (IReadChunkEscalationsRepository, IWriteChunkEscalationsRepository),
    "events": (IReadChunkEventsRepository, IWriteChunkEventsRepository),
    "usage": (IReadChunkUsageRepository, IWriteChunkUsageRepository),
    "delivery": (IReadChunkDeliveryRepository, IWriteChunkDeliveryRepository),
    "hub_exec": (IReadChunkHubExecRepository, IWriteChunkHubExecRepository),
    "dependencies": (IReadChunkDependenciesRepository, IWriteChunkDependenciesRepository),
}


def transition_identity_violations(stores: TenantStores, services: TenantServices) -> list[str]:
    """Each retained bag field that is not its container instance, and each port that is not its adapter's."""
    tenant = stores.tenant
    retained: tuple[tuple[str, object, type[Any]], ...] = (
        ("TenantStores.connections", stores.connections, ScopedConnections),
        ("TenantStores.transactions", stores.transactions, HubTransactions),
        ("TenantStores.projects", stores.projects, ProjectStore),
        ("TenantStores.finding_store", stores.finding_store, FindingStore),
        ("TenantStores.registry", stores.registry, RunnerRegistryStore),
        ("TenantStores.hosts", stores.hosts, HostRegistryStore),
        ("TenantStores.work_item_store", stores.work_item_store, WorkItemStore),
        ("TenantStores.chunk_stores", stores.chunk_stores, ChunkStores),
        ("TenantStores.secrets", stores.secrets, SecretStore),
        ("TenantStores.work_source_records", stores.work_source_records, WorkSourceRecordStore),
        ("TenantStores.repository_records", stores.repository_records, RepositoryRecordStore),
        ("TenantStores.forge_records", stores.forge_records, ForgeRecordStore),
        ("TenantStores.credential_records", stores.credential_records, CredentialRecordStore),
        ("TenantStores.secret_referrers", stores.secret_referrers, SecretReferrersStore),
        ("TenantStores.forge_referrers", stores.forge_referrers, ForgeReferrersStore),
        ("TenantStores.credential_referrers", stores.credential_referrers, CredentialReferrersStore),
        ("TenantStores.config_changes", stores.config_changes, ConfigChangeStore),
        ("TenantStores.project_links", stores.project_links, ProjectLinkStore),
        ("TenantStores.config_apply", stores.config_apply, ConfigApplyStore),
        ("TenantStores.config_import", stores.config_import, ConfigImportStore),
        ("TenantStores.invitations", stores.invitations, InvitationStore),
        ("TenantStores.urgencies", stores.urgencies, UrgencyStore),
        ("TenantStores.urgency_order", stores.urgency_order, UrgencyOrderStore),
        ("TenantServices.graphs", services.graphs, GraphStore),
        ("TenantServices.graph_bodies", services.graph_bodies, GraphStore),
        ("TenantServices.node_executors", services.node_executors, GraphStore),
        ("TenantServices.work_source_records", services.work_source_records, WorkSourceRecordStore),
        ("TenantServices.repository_records", services.repository_records, RepositoryRecordStore),
        ("TenantServices.forge_records", services.forge_records, ForgeRecordStore),
        ("TenantServices.credential_records", services.credential_records, CredentialRecordStore),
        ("TenantServices.projects", services.projects, IReadProjectRepository),
        ("TenantServices.project_links", services.project_links, IReadProjectLinks),
        ("TenantServices.chunk_targets", services.chunk_targets, ChunkTargets),
        ("TenantServices.findings", services.findings, IReadFindingRepository),
        ("TenantServices.finding_bucket", services.finding_bucket, FindingBucketReader),
        ("TenantServices.graph_yaml", services.graph_yaml, IConfigCodec),
        ("TenantServices.graph_mint", services.graph_mint, GraphMintService),
        ("TenantServices.default_graph", services.default_graph, DefaultGraphService),
        ("TenantServices.urgency_reading", services.urgency_reading, UrgencyReading),
        ("TenantServices.events", services.events, EventBroker),
        ("TenantServices.registry", services.registry, IReadRunnerRegistry),
        ("TenantServices.hosts", services.hosts, IReadHostRegistry),
        ("TenantStores.work_item_edits", stores.work_item_edits, WorkItemEditService),
        ("TenantServices.event_log", services.event_log, EventLogService),
        ("TenantServices.flows", services.flows, FlowStore),
    )
    problems = [
        f"{name} is not its container instance" for name, field, key in retained if field is not tenant.get(key)
    ]
    for adapter, ports in ADAPTER_PORTS.items():
        owned = tenant.get(adapter)
        problems.extend(
            f"{each.__qualname__} is not {adapter.__qualname__}'s instance"
            for each in ports
            if tenant.get(each) is not owned
        )
    chunks = tenant.get(ChunkStores)
    for field, ports in CHUNK_PORTS.items():
        problems.extend(
            f"{each.__qualname__} is not ChunkStores.{field}"
            for each in ports
            if tenant.get(each) is not getattr(chunks, field)
        )
    return problems


def process_identity_violations(core: HubCore) -> list[str]:
    """Each retained hub-level field, and each process port, that is not the process container's instance."""
    process = core.process.resolver
    retained: tuple[tuple[str, object, Any], ...] = (
        ("HubCore.hub_scoped_reads", core.hub_scoped_reads, HubScopedReads),
        ("IReadHubEventLog", process.get(IReadHubEventLog), HubEventLogStore),
        ("IWriteHubEventLog", process.get(IWriteHubEventLog), HubEventLogStore),
        ("IHubInvariantChecker", process.get(IHubInvariantChecker), HubInvariants),
        ("IReadTraceSteps", process.get(IReadTraceSteps), TraceStore),
        ("IReadTraceStatus", process.get(IReadTraceStatus), TraceStore),
        ("IWriteTraceCursor", process.get(IWriteTraceCursor), TraceStore),
        ("IReadEgress", process.get(IReadEgress), EgressStore),
        ("IWriteEgressCursor", process.get(IWriteEgressCursor), EgressStore),
        ("HubActions.catalogue", process.get(HubActions).catalogue, HubActionCatalogue),
    )
    return [f"{name} is not its container instance" for name, field, key in retained if field is not process.get(key)]


#: The process values every tenant scope resolves to the process scope's one instance.
PROCESS_SHARED: tuple[type[Any], ...] = (
    HubActions,
    HubActionCatalogue,
    IConfigCodec,
    PackagedGraphs,
    PackagedSystemArtifacts,
)


def process_sharing_violations(core: HubCore, stores: TenantStores) -> list[str]:
    """Each process value the tenant scope resolves to an instance other than the process scope's."""
    process = core.process.resolver
    return [
        f"{key.__qualname__} is not the process instance"
        for key in PROCESS_SHARED
        if stores.tenant.get(key) is not process.get(key)
    ]
