"""The observability area as the hub container holds it: each tenant key is one instance every holder shares and no
other tenant holds, the hub event log and the invariant check are each one process instance the legacy consumers hold,
and the derivation and annotation passes reach the very instances a request beneath the tenant resolves (component
tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.foundation.registry.scopes import ScopeValues
from blizzard.hub.domain.chunk.ports.work_sources import IWorkAnnotators
from blizzard.hub.domain.observability.analytics.derivation import EventDerivationService, TenantEventDerivation
from blizzard.hub.domain.observability.analytics.events import IReadTranscriptEvents, IWriteTranscriptEvents
from blizzard.hub.domain.observability.analytics.operational import IReadOperationalAnalytics
from blizzard.hub.domain.observability.analytics.queries import IReadAnalyticsEventQueries
from blizzard.hub.domain.observability.env_timeline import EnvTimelineReading, IReadEnvTimelineFacts
from blizzard.hub.domain.observability.forge_status import (
    IReadAnnotatedSources,
    IWriteAnnotatedSources,
    TenantAnnotation,
)
from blizzard.hub.domain.observability.hub_events import HubEventLogService, IReadHubEventLog, IWriteHubEventLog
from blizzard.hub.domain.observability.invariants import HubInvariantCheck, IHubInvariantChecker
from blizzard.hub.domain.observability.transcripts import (
    IReadTranscriptSegments,
    IWriteTranscriptSegments,
    TranscriptCaps,
    TranscriptIngestService,
)
from blizzard.hub.store.internal.analytics_event_query_store import AnalyticsEventQueryStore
from blizzard.hub.store.internal.analytics_operational_store import AnalyticsOperationalStore
from blizzard.hub.store.internal.env_timeline_store import EnvTimelineStore
from blizzard.hub.store.internal.forge_annotation_store import ForgeAnnotationStore
from blizzard.hub.store.internal.hub_event_log_store import HubEventLogStore
from blizzard.hub.store.internal.invariants import HubInvariants
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.tenancy import StoreScope
from blizzard.hub.work_sources.source import IWorkSourceRegistry
from tests.support.scaffolding import HubHarness, add_tenant, build_hub, default_tenant

# The services hold what they were built over privately; reading it is the only way to see it is shared.
# pyright: reportPrivateUsage=false

#: Each tenant key the area or its adapters own.
_TENANT_KEYS: tuple[type[object], ...] = (
    TranscriptSegmentStore,
    TranscriptEventStore,
    AnalyticsEventQueryStore,
    AnalyticsOperationalStore,
    EnvTimelineStore,
    ForgeAnnotationStore,
    TranscriptIngestService,
    EventDerivationService,
    TenantEventDerivation,
    TenantAnnotation,
    EnvTimelineReading,
)


def _held(hub: HubHarness, tenant: StoreScope) -> dict[type[object], object]:
    """The tenant scope's instance of each tenant key."""
    return {key: hub.hub_services.process.tenants.resolve(tenant, key) for key in _TENANT_KEYS}


def test_each_tenant_key_is_one_instance_every_holder_shares(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = StoreScope(default_tenant(hub.engine))
    tenants = hub.hub_services.process.tenants
    held = _held(hub, tenant)

    ports = {
        IReadTranscriptSegments: TranscriptSegmentStore,
        IWriteTranscriptSegments: TranscriptSegmentStore,
        IReadTranscriptEvents: TranscriptEventStore,
        IWriteTranscriptEvents: TranscriptEventStore,
        IReadAnalyticsEventQueries: AnalyticsEventQueryStore,
        IReadOperationalAnalytics: AnalyticsOperationalStore,
        IReadEnvTimelineFacts: EnvTimelineStore,
        IReadAnnotatedSources: ForgeAnnotationStore,
        IWriteAnnotatedSources: ForgeAnnotationStore,
    }
    for each, adapter in ports.items():
        assert tenants.resolve(tenant, each) is held[adapter], each.__qualname__

    ingest = held[TranscriptIngestService]
    derivation = held[EventDerivationService]
    tenant_derivation = held[TenantEventDerivation]
    annotation = held[TenantAnnotation]
    timeline = held[EnvTimelineReading]
    assert isinstance(ingest, TranscriptIngestService) and isinstance(derivation, EventDerivationService)
    assert isinstance(tenant_derivation, TenantEventDerivation) and isinstance(annotation, TenantAnnotation)
    assert isinstance(timeline, EnvTimelineReading)
    assert ingest._store is held[TranscriptSegmentStore]
    assert derivation._events is held[TranscriptEventStore] is tenant_derivation._events
    assert tenant_derivation._service is derivation
    assert annotation._memory is held[ForgeAnnotationStore]
    assert annotation._work_sources is tenants.resolve(tenant, IWorkAnnotators)
    assert tenants.resolve(tenant, IWorkAnnotators) is tenants.resolve(tenant, IWorkSourceRegistry)
    assert timeline._facts is held[EnvTimelineStore]

    request = tenants.request(tenant, ScopeValues())
    try:
        resolved = {key: request.resolver.get(key) for key in _TENANT_KEYS}
    finally:
        request.close()
    assert [key.__qualname__ for key, instance in resolved.items() if instance is not held[key]] == []


def test_no_tenant_owned_observability_instance_is_shared_across_tenants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = _held(hub, StoreScope(default_tenant(hub.engine)))
    second_tenant = StoreScope(add_tenant(hub.engine, "second"))
    hub.hub_services.tenants.open_tenants.refresh()
    second = _held(hub, second_tenant)

    assert [key.__qualname__ for key, instance in first.items() if instance is second[key]] == []


def test_the_hub_event_log_and_invariant_check_are_each_one_process_instance_the_legacy_consumers_hold(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path)
    process = hub.hub_services.process.resolver
    event_log = process.get(HubEventLogService)
    check = process.get(HubInvariantCheck)
    tenant = StoreScope(default_tenant(hub.engine))
    assert hub.core is not None

    assert hub.hub_services.process.tenants.resolve(tenant, HubEventLogService) is event_log
    assert hub.core.hub_event_log is event_log
    assert hub.hub_services.hub_event_log is event_log
    assert hub.hub_services.tenant_teardown._events is event_log
    assert hub.hub_services.egress_reset._events is event_log
    assert event_log._events is process.get(HubEventLogStore)
    assert process.get(IReadHubEventLog) is process.get(IWriteHubEventLog) is process.get(HubEventLogStore)
    assert hub.core.hub_events is process.get(HubEventLogStore)

    assert check._checker is process.get(IHubInvariantChecker) is process.get(HubInvariants)


def test_the_reconcilers_accessors_return_the_request_scopes_instance(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    tenant = StoreScope(default_tenant(hub.engine))
    derivation = hub.hub_services.event_derivation
    annotation = hub.hub_services.annotation
    assert annotation is not None

    request = hub.hub_services.process.tenants.request(tenant, ScopeValues())
    try:
        assert derivation._tenant(tenant.tenant) is request.resolver.get(TenantEventDerivation)
        assert annotation._tenant(tenant.tenant) is request.resolver.get(TenantAnnotation)
    finally:
        request.close()


def test_the_ingest_falls_back_to_the_boot_configs_transcript_caps(tmp_path: Path) -> None:
    caps = TranscriptCaps(record_max_bytes=200)

    hub = build_hub(tmp_path, transcript_caps=caps)

    assert hub.hub_services.process.resolver.get(TranscriptCaps) == caps
    assert hub.default_adapter(TranscriptIngestService)._caps == caps
