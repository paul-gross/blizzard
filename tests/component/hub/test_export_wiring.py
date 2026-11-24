"""The trace and egress exports as the hub container holds them: the roster sweeps and the routes serve the process's
one instance of each, every trace and egress port is its store's instance, a tenant's step facts are hydrated only
through the tenant accessor the root binds, and the container closes neither the writers nor the lent exporter
(component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.foundation.registry.scopes import ScopeValues
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.hub.composition import build_hub_core, connections_for_engine
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.observability.egress.backfill import EgressBackfill
from blizzard.hub.domain.observability.egress.boot import EgressPathKey
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.ports import IEgressWriter
from blizzard.hub.domain.observability.egress.repository import IReadEgress, IReadEgressEvents, IWriteEgressCursor
from blizzard.hub.domain.observability.egress.reset import EgressReset
from blizzard.hub.domain.observability.egress.status import EgressStatusReader
from blizzard.hub.domain.observability.egress.sweep import EgressSweep
from blizzard.hub.domain.observability.hub_events import HubEventLogService
from blizzard.hub.domain.observability.tracing.replay import TraceReplay
from blizzard.hub.domain.observability.tracing.repository import (
    IReadTraceStatus,
    IReadTraceSteps,
    IWriteTraceCursor,
    StepHydration,
    StepHydrations,
)
from blizzard.hub.domain.observability.tracing.status import TraceStatusReader
from blizzard.hub.domain.observability.tracing.sweep import TraceExportSweep
from blizzard.hub.domain.sweeps.names import SweepName
from blizzard.hub.egress.factory import BackfillWriters, ConfiguredWriter
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.trace_store import TraceStore
from blizzard.hub.tenancy import StoreScope, TenantClosed, UnknownTenant
from tests.support.scaffolding import (
    TEST_HUB_ACTIONS,
    HubHarness,
    InMemoryTraceExporter,
    build_hub,
    close_tenant,
    default_tenant,
)

# The services hold what they were built over privately; reading it is the only way to see it is shared.
# pyright: reportPrivateUsage=false

#: The services every export-wired hub serves from its process scope, its sweeps aside.
_SERVED: tuple[type[object], ...] = (TraceStatusReader, TraceReplay, EgressStatusReader, EgressReset, EgressBackfill)


def _exporting(tmp_path: Path, exporter: InMemoryTraceExporter) -> HubHarness:
    return build_hub(
        tmp_path,
        trace_exporter=exporter,
        tracing=TracingConfig(settle_seconds=0),
        egress=EgressConfig(directory=tmp_path / "out", settle_seconds=0),
        egress_path_key=b"k" * 32,
    )


def test_the_roster_and_the_routes_serve_the_containers_export_instances(tmp_path: Path) -> None:
    exporter = InMemoryTraceExporter()
    hub = _exporting(tmp_path, exporter)
    process = hub.hub_services.process.resolver
    trace_sweep = process.get(TraceExportSweep | None)
    egress_sweep = process.get(EgressSweep | None)
    assert isinstance(trace_sweep, TraceExportSweep) and isinstance(egress_sweep, EgressSweep)

    roster = hub.hub_services.sweeps._every_tenant
    assert roster[SweepName.TRACE_EXPORT] is trace_sweep is process.get(TraceExportSweep)
    assert roster[SweepName.EGRESS] is egress_sweep is process.get(EgressSweep)

    request = hub.hub_services.process.tenants.request(StoreScope(default_tenant(hub.engine)), ScopeValues())
    try:
        assert [key.__qualname__ for key in _SERVED if request.resolver.get(key) is not process.get(key)] == []
    finally:
        request.close()

    trace = process.get(TraceStore)
    egress = process.get(EgressStore)
    assert process.get(IReadTraceSteps) is process.get(IReadTraceStatus) is process.get(IWriteTraceCursor) is trace
    assert process.get(IReadEgress) is process.get(IWriteEgressCursor) is egress
    assert process.get(IReadEgressEvents) is hub.hub_services.hub_scoped_reads.corpus
    event_log = process.get(HubEventLogService)
    assert trace_sweep._steps is trace and trace_sweep._exporter is exporter and trace_sweep._events is event_log
    assert process.get(TraceReplay)._exporter is exporter
    assert egress_sweep._steps is trace and egress_sweep._egress is egress
    assert egress_sweep._writer is process.get(IEgressWriter) and egress_sweep._events is event_log
    assert egress_sweep._writer_for == process.get(ConfiguredWriter).for_config
    assert process.get(EgressBackfill)._writer_for is process.get(BackfillWriters)


def test_with_both_exports_off_neither_sweep_is_built_and_the_services_still_answer(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    process = hub.hub_services.process.resolver

    assert process.get(TraceExportSweep | None) is None
    assert process.get(EgressSweep | None) is None
    names = hub.hub_services.sweeps.names()
    assert SweepName.TRACE_EXPORT not in names and SweepName.EGRESS not in names
    assert process.get(TraceReplay)._exporter is None
    assert process.get(EgressBackfill)._writer_for is None
    assert not process.get(EgressReset)._active


def test_step_facts_are_hydrated_through_the_tenant_accessor_and_a_closed_tenant_refuses(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    process = hub.hub_services.process
    hydrations = process.resolver.get(StepHydrations)
    tenant = default_tenant(hub.engine)

    assert process.resolver.get(TraceStore)._hydration is hydrations
    assert hydrations(tenant) is process.tenants.resolve(StoreScope(tenant), StepHydration)

    close_tenant(hub.engine, tenant)
    hub.hub_services.tenants.open_tenants.refresh()
    process.tenants.evict(tenant)
    with pytest.raises((TenantClosed, UnknownTenant)):
        hydrations(tenant)


class _ClosableExporter(InMemoryTraceExporter):
    """An exporter that counts the closes it is handed."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def test_closing_the_process_leaves_the_lent_exporter_open(tmp_path: Path) -> None:
    built = build_hub(tmp_path)
    exporter = _ClosableExporter()
    assert built.config is not None
    config = HubConfig(
        root=built.config.root, db_url=built.config.db_url, egress=EgressConfig(directory=tmp_path / "out")
    )
    core = build_hub_core(
        connections_for_engine(built.engine),
        clock=FixedClock(built.clock.now()),
        config=config,
        platform_tracing=DisabledPlatformTracing(),
        environ={},
        actions=TEST_HUB_ACTIONS,
        trace_exporter=exporter,
        egress_path_key=EgressPathKey(b"k" * 32),
    )
    process = core.process.resolver
    assert process.get(TraceExportSweep | None) is not None
    writer = process.get(IEgressWriter)
    assert process.get(ConfiguredWriter).for_config(config.egress) is writer

    core.process.close()

    assert exporter.closed == 0
