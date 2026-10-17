"""Reconcile the packaged graph set against the store — ``blizzard hub graph sync`` (#146).

The edge half: walk the packaged set, load and inline each ``graph.yaml`` (filesystem and
the config codec, both outside the domain — ``bzh:domain-core``), and hand it with the stored definition
to the domain, which owns the mint-only-if-changed rule. Per-graph isolated — one graph
failing to load is a report row, not a stop — and additive: nothing re-pins a chunk. A hub reconciles every open
tenant in turn, each through its own stores (:func:`reconcile_open_tenants`), and a tenant's creation mints the set
into it on the creation's own transaction (:func:`packaged_minter`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.documents.codec import ConfigDecodeError, IConfigCodec
from blizzard.hub.domain.graph.authoring import GraphMintService
from blizzard.hub.domain.graph.model import GraphDoc, GraphParseError, IReadGraphRepository, IWriteGraphRepository
from blizzard.hub.domain.graph.validation import GraphValidationError
from blizzard.hub.graphs import PACKAGED, GraphFile
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant

_log = get_logger("blizzard.hub.graph_sync")


class GraphSyncStatus(StrEnum):
    """What reconciliation did with one packaged graph."""

    MINTED = "minted"
    UP_TO_DATE = "up-to-date"
    FAILED = "failed"


@domain_model
@dataclass(frozen=True)
class GraphSyncOutcome:
    """One packaged graph's reconciliation result — a row of the report.

    ``name`` is the authored name where one could be read, else the packaged directory
    name. ``detail`` explains a mint or a failure, and is ``None`` for up-to-date."""

    name: str
    status: GraphSyncStatus
    graph_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class GraphReconciliation:
    """The packaged graph set reconciled against the store, one report row per graph."""

    mint_service: GraphMintService
    graphs: IReadGraphRepository
    #: The YAML binding a definition is stored and re-read through.
    yaml: IConfigCodec
    #: Overrides the packaged set, defaulting to :attr:`~blizzard.hub.graphs.PackagedGraphs.paths`.
    paths: list[Path] | None = None

    def outcomes(self) -> list[GraphSyncOutcome]:
        """Reconcile every packaged graph against the store; mint only what changed.

        Idempotent: a second run against an unchanged packaged set mints nothing."""
        return [self._outcome(path) for path in (self.paths if self.paths is not None else PACKAGED.paths)]

    def _outcome(self, path: Path) -> GraphSyncOutcome:
        """One packaged graph, with every failure mode folded into a report row.

        The load and the compare are separate ``try`` blocks: a graph that cannot be loaded
        has no authored name to report a validation failure under."""
        try:
            packaged = GraphFile(path)
            doc = packaged.doc
            definition_yaml = packaged.inlined_yaml(self.yaml)
        except (OSError, ValueError) as exc:
            # ValueError covers GraphParseError (its base) and the loader's "not a mapping".
            _log.warning("packaged graph failed to load", path=str(path), error=str(exc))
            return GraphSyncOutcome(name=path.parent.name, status=GraphSyncStatus.FAILED, detail=str(exc))

        stored = self.graphs.newest_definition_yaml(doc.name)
        try:
            graph = self.mint_service.mint_if_changed(
                doc, definition_yaml=definition_yaml, minted=self._parsed(stored, doc.name)
            )
        except GraphValidationError as exc:
            _log.warning("packaged graph failed validation", graph=doc.name, error=str(exc))
            return GraphSyncOutcome(name=doc.name, status=GraphSyncStatus.FAILED, detail="; ".join(exc.result.errors))

        if graph is None:
            return GraphSyncOutcome(name=doc.name, status=GraphSyncStatus.UP_TO_DATE)
        reason = "first of its name" if stored is None else "packaged definition differs from the newest mint"
        _log.info("graph minted by reconciliation", graph=doc.name, graph_id=graph.graph_id, reason=reason)
        return GraphSyncOutcome(name=doc.name, status=GraphSyncStatus.MINTED, graph_id=graph.graph_id, detail=reason)

    def _parsed(self, stored: str | None, name: str) -> GraphDoc | None:
        """A stored definition re-parsed into an authoring doc, or ``None``.

        ``None`` means "nothing to compare against", which mints. It covers both the
        never-minted name and the stored definition that no longer parses — that one stays in
        the store, superseded rather than lost."""
        if stored is None:
            return None
        try:
            return GraphDoc.of(self.yaml.decode(stored.encode("utf-8")))
        except (ConfigDecodeError, GraphParseError):
            _log.warning("stored graph definition no longer parses; treating it as changed", graph=name)
            return None


@domain_model
@dataclass(frozen=True)
class TenantGraphOutcomes:
    """One tenant's reconciliation: the tenant, its name, and a report row per packaged graph."""

    tenant: TenantId
    name: str
    outcomes: list[GraphSyncOutcome]


def reconcile_open_tenants(
    open_tenants: Mapping[TenantId, str], reconciliation_of: Callable[[TenantId], GraphReconciliation]
) -> list[TenantGraphOutcomes]:
    """Reconcile the packaged set into each of ``open_tenants``, by id, through the reconciliation
    ``reconciliation_of`` opens over that tenant's own stores. A tenant closed since the set was read — before its
    stores open, or between their opening and a mint, which its closed tenant lock refuses — is passed over and left
    out of the answer; every later tenant still reconciles."""
    reports: list[TenantGraphOutcomes] = []
    for tenant, name in sorted(open_tenants.items()):
        try:
            outcomes = reconciliation_of(tenant).outcomes()
        except (TenantClosed, UnknownTenant):
            _log.info("tenant closed during its graph sync; passed over", tenant=tenant)
            continue
        reports.append(TenantGraphOutcomes(tenant=tenant, name=name, outcomes=outcomes))
    return reports


def packaged_minter(clock: IClock, yaml: IConfigCodec) -> Callable[[IWriteGraphRepository], list[GraphSyncOutcome]]:
    """Reconciles the packaged set into the graphs it is handed — a new tenant's, on its creation's transaction."""

    def mint(graphs: IWriteGraphRepository) -> list[GraphSyncOutcome]:
        return GraphReconciliation(GraphMintService(graphs=graphs, clock=clock), graphs, yaml).outcomes()

    return mint
