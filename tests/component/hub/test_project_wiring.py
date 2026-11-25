"""The projects area's registrations as the hub container holds them: within a tenant each project port, service and
retained bag field is one instance; across tenants the instances differ, except the color draw, which every tenant
shares (component tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.domain.projects.authoring import ProjectAuthoring
from blizzard.hub.domain.projects.bulk_resolve import BulkProjectResolver, IBulkProjectLookup
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.domain.projects.links import IReadProjectLinks, IWriteProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository, IWriteProjectRepository
from blizzard.hub.domain.projects.order import IReadProjectPositions, IWriteProjectPositions
from blizzard.hub.domain.projects.targets import ChunkTargetCapture, ChunkTargets, IReadChunkTargets, IWriteChunkTargets
from blizzard.hub.store.internal.chunk_targets_store import ChunkTargetsStore
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.project_position_store import ProjectPositionStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.tenancy import StoreScope
from tests.support.scaffolding import HubHarness, add_tenant, build_hub, default_tenant


def _shared(hub: HubHarness, tenant: StoreScope) -> dict[str, list[object]]:
    """Every holder of each project-area instance in ``tenant``, by what the instance is."""
    scope = hub.process.tenants.open(tenant).resolver
    services = hub.hub_services.tenants.open_services(tenant)
    return {
        "project records": [
            scope.get(ProjectStore),
            scope.get(IReadProjectRepository),
            scope.get(IWriteProjectRepository),
            scope.get(IBulkProjectLookup),
            services.projects,
        ],
        "project links": [
            scope.get(ProjectLinkStore),
            scope.get(IReadProjectLinks),
            scope.get(IWriteProjectLinks),
            services.project_links,
        ],
        "project positions": [
            scope.get(ProjectPositionStore),
            scope.get(IReadProjectPositions),
            scope.get(IWriteProjectPositions),
        ],
        "chunk target records": [
            scope.get(ChunkTargetsStore),
            scope.get(IReadChunkTargets),
            scope.get(IWriteChunkTargets),
        ],
        "project authoring": [scope.get(ProjectAuthoring)],
        "bulk project resolver": [scope.get(BulkProjectResolver)],
        "chunk targets": [scope.get(ChunkTargets), services.chunk_targets],
        "chunk target capture": [scope.get(ChunkTargetCapture)],
    }


def test_the_project_areas_keys_share_identity_within_a_tenant_and_differ_across_tenants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = StoreScope(default_tenant(hub.engine))
    second = StoreScope(add_tenant(hub.engine, "second"))
    hub.hub_services.tenants.open_tenants.refresh()

    mine, theirs = _shared(hub, first), _shared(hub, second)

    for name, held in (*mine.items(), *theirs.items()):
        assert all(each is held[0] for each in held), name
    for name in mine:
        assert mine[name][0] is not theirs[name][0], name


def test_every_tenant_shares_the_one_color_draw(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = StoreScope(default_tenant(hub.engine))
    second = StoreScope(add_tenant(hub.engine, "second"))
    hub.hub_services.tenants.open_tenants.refresh()
    tenants = hub.process.tenants

    process_draw = hub.process.resolver.get(ColorPick)

    assert tenants.open(first).resolver.get(ColorPick) is process_draw
    assert tenants.open(second).resolver.get(ColorPick) is process_draw
