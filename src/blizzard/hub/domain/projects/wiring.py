"""The projects area's registrations: its project, link, position and chunk-target ports over the adapters it is handed,
the services over them, and the color draw a project created with none leaves to the hub.

Each port aliases the one adapter instance the root hands in, so a reader and a writer in one tenant share it; the area
names no adapter. The store-free graph declares each registration absent, from the same roster."""

from __future__ import annotations

import random
from typing import Any, cast

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.projects.authoring import ProjectAuthoring
from blizzard.hub.domain.projects.bulk_resolve import BulkProjectResolver, IBulkProjectLookup
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.domain.projects.links import IReadProjectLinks, IWriteProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository, IWriteProjectRepository
from blizzard.hub.domain.projects.order import IReadProjectPositions, IWriteProjectPositions
from blizzard.hub.domain.projects.targets import (
    ChunkTargetCapture,
    ChunkTargets,
    IReadChunkTargets,
    IWriteChunkTargets,
)
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def color_pick() -> ColorPick:
    """One process-scoped draw for every project color a create leaves to the hub."""
    return random.Random().choice


def register(
    registry: Registry,
    *,
    projects: Ref[IWriteProjectRepository],
    links: Ref[IWriteProjectLinks],
    positions: Ref[IWriteProjectPositions],
    chunk_targets: Ref[IWriteChunkTargets],
) -> None:
    """Declare the project ports over the adapters handed in, each typed by the widest port it answers to, and the
    services over them, each once per tenant — the color draw once per process."""
    registry.alias(port(IReadProjectRepository), projects)
    registry.alias(port(IWriteProjectRepository), projects)
    registry.alias(port(IBulkProjectLookup), projects)
    registry.alias(port(IReadProjectLinks), links)
    registry.alias(port(IWriteProjectLinks), links)
    registry.alias(port(IReadProjectPositions), positions)
    registry.alias(port(IWriteProjectPositions), positions)
    registry.alias(port(IReadChunkTargets), chunk_targets)
    registry.alias(port(IWriteChunkTargets), chunk_targets)
    registry.factory(color_pick, lifetime=Lifetime.PROCESS)
    registry.service(ProjectAuthoring, lifetime=Lifetime.TENANT)
    registry.service(BulkProjectResolver, lifetime=Lifetime.TENANT)
    registry.service(ChunkTargets, lifetime=Lifetime.TENANT)
    registry.service(ChunkTargetCapture, lifetime=Lifetime.TENANT)


#: Every tenant key :func:`register` declares, for the store-free graph to declare absent.
_TENANT_KEYS: tuple[type[Any], ...] = (
    IReadProjectRepository,
    IWriteProjectRepository,
    IBulkProjectLookup,
    IReadProjectLinks,
    IWriteProjectLinks,
    IReadProjectPositions,
    IWriteProjectPositions,
    IReadChunkTargets,
    IWriteChunkTargets,
    ProjectAuthoring,
    BulkProjectResolver,
    ChunkTargets,
    ChunkTargetCapture,
)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports, services and color draw absent, for the store-free graph."""
    for missing in _TENANT_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    # A callable alias keys the color draw, so it is named as its key rather than as a class.
    registry.absent(port(cast("type[Any]", ColorPick)), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
