"""The API edge's registrations: each tenant's one chunk-frame loader, over the frame reads and the tenant's broker, the
reads a flow line and a chunk read render from, and the hub's version window, the process value a root serving runners
supplies.

The store-free graph declares the tenant records absent, and a root that supplies no window declares the window
absent."""

from __future__ import annotations

from typing import Any, cast

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.api.chunk_events import announce_minted
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.chunk_views import ChunkViewReads
from blizzard.hub.api.flow_places import FlowLineReads
from blizzard.hub.api.garden_formats import GardenFormats
from blizzard.hub.api.queue import QueueReads
from blizzard.hub.api.version_window import HubVersionWindow
from blizzard.hub.domain.cadence.sweep import MintAnnouncement
from blizzard.hub.domain.garden.proposals.closure import DefaultGraphEnsurer
from blizzard.hub.domain.graph.authoring import DefaultGraphService
from blizzard.hub.graphs import PackagedDefaultGraph, packaged_default_graph
from blizzard.hub.tenancy import STORE_UNAVAILABLE

#: The tenant records the edge registers by class.
_TENANT_RECORDS: tuple[type[Any], ...] = (ChunkFrameLoader, FlowLineReads, ChunkViewReads, QueueReads)


def default_graph_ensurer(default_graph: DefaultGraphService, packaged: PackagedDefaultGraph) -> DefaultGraphEnsurer:
    """Ensures the tenant's default graph, minted from the packaged one when none has ever existed."""
    return lambda: default_graph.ensure_default(packaged.doc, definition_yaml=packaged.yaml)


def mint_announcement(frames: ChunkFrameLoader) -> MintAnnouncement:
    """Publishes a committed cadence mint's frames through the tenant's chunk-frame loader."""
    return lambda minted: announce_minted(frames, minted)


def register(registry: Registry) -> Ref[GardenFormats]:
    """Declare the tenant's chunk-frame loader and the edge's read records, once per tenant, the default graph a
    garden closure mints against, the announcement a cadence mint publishes, and the packaged default graph and the
    garden artifact formats once per process, handing back the formats' ``Ref``, which the garden area's port
    aliases."""
    for record in _TENANT_RECORDS:
        registry.service(record, lifetime=Lifetime.TENANT)
    registry.factory(default_graph_ensurer, lifetime=Lifetime.TENANT)
    registry.factory(mint_announcement, lifetime=Lifetime.TENANT)
    registry.factory(packaged_default_graph, lifetime=Lifetime.PROCESS)
    return registry.service(GardenFormats, lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the tenant records absent, for the store-free graph."""
    for missing in _TENANT_RECORDS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    registry.absent(port(cast("type[Any]", MintAnnouncement)), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    registry.absent(port(PackagedDefaultGraph), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    registry.absent(port(GardenFormats), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)


def register_version_window(registry: Registry) -> Ref[HubVersionWindow]:
    """Declare the hub's version window as the process value the root supplies, handing back its ``Ref``."""
    return registry.context(port(HubVersionWindow), lifetime=Lifetime.PROCESS)


def register_version_window_absent(registry: Registry) -> None:
    """Declare the hub's version window absent, for a root that supplies none."""
    registry.absent(port(HubVersionWindow), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
