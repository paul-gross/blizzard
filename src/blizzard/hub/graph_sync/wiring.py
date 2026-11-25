"""The graph sync's registrations: the packaged-graph mint a tenant's creation runs, each tenant's graph
reconciliation, and the reconciliation accessor the root supplies. The store-free graph declares each absent."""

from __future__ import annotations

from typing import Any, cast

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.documents.codec import IConfigCodec
from blizzard.hub.domain.graph.actions import HubActionCatalogue
from blizzard.hub.domain.graph.authoring import GraphMintService
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.domain.tenancy.administration import PackagedGraphMint
from blizzard.hub.graph_sync import GraphReconciliation, GraphReconciliations, packaged_minter
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def packaged_graph_mint(clock: IClock, yaml: IConfigCodec, catalogue: HubActionCatalogue) -> PackagedGraphMint:
    """Reconciles the packaged graph set into a new tenant's graphs, on its creation's transaction."""
    return packaged_minter(clock, yaml, catalogue)


def graph_reconciliation(
    mint: GraphMintService, graphs: IReadGraphRepository, yaml: IConfigCodec
) -> GraphReconciliation:
    """The tenant's packaged graph set reconciled against its own graphs."""
    return GraphReconciliation(mint, graphs, yaml)


def register(registry: Registry) -> None:
    """Declare the packaged-graph mint once per process, each tenant's graph reconciliation, and the reconciliation
    accessor the root supplies and binds over its tenant scopes."""
    registry.factory(packaged_graph_mint, lifetime=Lifetime.PROCESS)
    registry.factory(graph_reconciliation, lifetime=Lifetime.TENANT)
    registry.context(port(GraphReconciliations), lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the graph-sync keys absent, for the store-free graph."""
    registry.absent(port(cast("type[Any]", PackagedGraphMint)), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    registry.absent(port(GraphReconciliation), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    registry.absent(port(GraphReconciliations), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
