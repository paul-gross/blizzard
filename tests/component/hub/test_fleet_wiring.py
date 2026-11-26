"""The fleet registrations as the hub container holds them: within a tenant each fleet port and service is one
instance, and the execution services hold the container's retired-runner guard, detach path and fleet
service; across tenants the instances differ, except the version window, which every tenant shares (component
tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.api.version_window import HubVersionWindow
from blizzard.hub.domain.execution.apply import ApplyService
from blizzard.hub.domain.execution.claim import ClaimService
from blizzard.hub.domain.execution.decisions import DecisionService
from blizzard.hub.domain.execution.detach import DetachService
from blizzard.hub.domain.execution.facts import FactIngestService
from blizzard.hub.domain.execution.fleet import FleetService
from blizzard.hub.domain.execution.host_facts import HostFactIngestService
from blizzard.hub.domain.execution.host_retirement import HostRetirementService
from blizzard.hub.domain.observability.transcripts import TranscriptIngestService
from blizzard.hub.domain.projects.bulk_resolve import BulkProjectResolver
from blizzard.hub.domain.runners.enrollment import RunnerEnrollmentService
from blizzard.hub.domain.runners.host import IReadHostRegistry, IWriteHostRegistry
from blizzard.hub.domain.runners.host_enrollment import HostEnrollmentService
from blizzard.hub.domain.runners.host_report import HostReportService
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, IWriteRunnerRegistry, RetiredRunnerGuard
from blizzard.hub.domain.runners.served import IServedProjects
from blizzard.hub.domain.runners.version import IVersionWindow
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import StoreScope
from tests.support.scaffolding import HubHarness, add_tenant, build_hub, default_tenant


def _shared(hub: HubHarness, tenant: StoreScope) -> dict[str, list[object]]:
    """Every holder of each fleet instance in ``tenant``, by what the instance is."""
    scope = hub.process.tenants.open(tenant).resolver
    fleet = scope.get(FleetService)
    # The execution services hold what they were built over privately; reading it is the only way to see it is shared.
    return {
        "runner registry": [
            scope.get(RunnerRegistryStore),
            scope.get(IReadRunnerRegistry),
            scope.get(IWriteRunnerRegistry),
            scope.get(ClaimService).registry,
            fleet.registry,
        ],
        "host registry": [
            scope.get(HostRegistryStore),
            scope.get(IReadHostRegistry),
            scope.get(IWriteHostRegistry),
        ],
        "retired-runner guard": [
            scope.get(RetiredRunnerGuard),
            fleet._retired,  # pyright: ignore[reportPrivateUsage]
            scope.get(ClaimService)._retired,  # pyright: ignore[reportPrivateUsage]
            scope.get(ApplyService)._retired,  # pyright: ignore[reportPrivateUsage]
            scope.get(DecisionService)._retired,  # pyright: ignore[reportPrivateUsage]
            scope.get(FactIngestService)._retired,  # pyright: ignore[reportPrivateUsage]
            scope.get(TranscriptIngestService)._retired,  # pyright: ignore[reportPrivateUsage]
        ],
        "detach": [
            scope.get(DetachService),
            fleet._detach,  # pyright: ignore[reportPrivateUsage]
        ],
        "fleet": [
            fleet,
            scope.get(FactIngestService)._fleet,  # pyright: ignore[reportPrivateUsage]
            scope.get(HostRetirementService)._fleet,  # pyright: ignore[reportPrivateUsage]
        ],
        "served projects": [scope.get(IServedProjects), scope.get(BulkProjectResolver)],
        "runner enrollment": [scope.get(RunnerEnrollmentService)],
        "host enrollment": [scope.get(HostEnrollmentService)],
        "host report": [scope.get(HostReportService)],
        "host retirement": [scope.get(HostRetirementService)],
        "host facts": [scope.get(HostFactIngestService)],
    }


def test_the_fleet_keys_share_identity_within_a_tenant_and_differ_across_tenants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = StoreScope(default_tenant(hub.engine))
    second = StoreScope(add_tenant(hub.engine, "second"))
    hub.resolve_hub(OpenTenants).refresh()

    mine, theirs = _shared(hub, first), _shared(hub, second)

    for name, held in (*mine.items(), *theirs.items()):
        assert all(each is held[0] for each in held), name
    for (name, held), (_, other) in zip(mine.items(), theirs.items(), strict=True):
        assert held[0] is not other[0], name


def test_every_tenant_judges_versions_through_the_one_supplied_window(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, hub_version="0.3")
    first = StoreScope(default_tenant(hub.engine))
    second = StoreScope(add_tenant(hub.engine, "second"))
    hub.resolve_hub(OpenTenants).refresh()
    tenants = hub.process.tenants

    window = hub.process.resolver.get(HubVersionWindow)

    assert window.hub_version == "0.3"
    assert hub.process.resolver.get(IVersionWindow) is window
    assert tenants.open(first).resolver.get(IVersionWindow) is window
    assert tenants.open(second).resolver.get(IVersionWindow) is window
    assert tenants.open(first).resolver.get(FleetService)._versions is window  # pyright: ignore[reportPrivateUsage]
