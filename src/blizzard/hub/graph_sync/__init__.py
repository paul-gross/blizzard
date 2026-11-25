"""Reconcile the packaged graph set against the store — ``blizzard hub graph sync``.

The edge half: load and inline each packaged ``graph.yaml`` and hand it with the stored definition to
the domain, which owns the mint-only-if-changed rule and when an unreadable stored definition holds
instead. Per-graph isolated and additive: nothing re-pins a chunk. Every open tenant reconciles through
its own stores (:func:`reconcile_open_tenants`); a new tenant is minted the set on its creation's
transaction (:func:`packaged_minter`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.documents.codec import ConfigDecodeError, IConfigCodec
from blizzard.hub.domain.graph.actions import HubActionCatalogue
from blizzard.hub.domain.graph.authoring import GraphMintService, UnreadableDefinitionHeld, reconciliation_basis
from blizzard.hub.domain.graph.legacy_run import translated
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
            _log.warning("packaged graph failed to load", path=str(path), error=str(exc))
            return GraphSyncOutcome(name=path.parent.name, status=GraphSyncStatus.FAILED, detail=str(exc))

        stored = self.graphs.newest_definition_yaml(doc.name)
        minted = self.parsed(stored, doc.name)
        unreadable = stored is not None and minted is None
        try:
            basis = reconciliation_basis(
                doc.name,
                minted=minted,
                unreadable=unreadable,
                # The name has a stored mint here, so no enabled one means every mint is retired.
                every_mint_retired=unreadable and self.graphs.graph_id_of_enabled_name(doc.name) is None,
            )
            graph = self.mint_service.mint_if_changed(doc, definition_yaml=definition_yaml, minted=basis)
        except UnreadableDefinitionHeld as exc:
            _log.warning("retired graph held: its stored definition no longer parses", graph=doc.name)
            return GraphSyncOutcome(name=doc.name, status=GraphSyncStatus.FAILED, detail=str(exc))
        except GraphValidationError as exc:
            _log.warning("packaged graph failed validation", graph=doc.name, error=str(exc))
            return GraphSyncOutcome(name=doc.name, status=GraphSyncStatus.FAILED, detail="; ".join(exc.result.errors))

        if graph is None:
            return GraphSyncOutcome(name=doc.name, status=GraphSyncStatus.UP_TO_DATE)
        reason = "first of its name" if stored is None else "packaged definition differs from the newest mint"
        _log.info("graph minted by reconciliation", graph=doc.name, graph_id=graph.graph_id, reason=reason)
        return GraphSyncOutcome(name=doc.name, status=GraphSyncStatus.MINTED, graph_id=graph.graph_id, detail=reason)

    def parsed(self, stored: str | None, name: str) -> GraphDoc | None:
        """A stored definition re-parsed into an authoring doc, or ``None``.

        ``None`` is a never-minted name, or a stored definition that no longer parses
        (:func:`~blizzard.hub.domain.graph.authoring.reconciliation_basis` decides the latter). A
        stored ``run:`` reads as the action it named
        (:func:`~blizzard.hub.domain.graph.legacy_run.translated`)."""
        if stored is None:
            return None
        try:
            return translated(GraphDoc.of(self.yaml.decode(stored.encode("utf-8"))))
        except (ConfigDecodeError, GraphParseError) as exc:
            _log.warning("stored graph definition no longer parses", graph=name, error=str(exc))
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


class GraphReconciliations:
    """Each tenant's :class:`GraphReconciliation`, through the typed tenant accessor the process root binds over its
    tenant scopes once they are built; it refuses a tenant as that tenant's scope refuses."""

    def __init__(self) -> None:
        self._open: Callable[[TenantId], GraphReconciliation] | None = None

    def bind(self, open: Callable[[TenantId], GraphReconciliation]) -> None:
        self._open = open

    def __call__(self, tenant: TenantId) -> GraphReconciliation:
        assert self._open is not None  # bound by the process root before the process is handed out
        return self._open(tenant)


def packaged_minter(
    clock: IClock, yaml: IConfigCodec, catalogue: HubActionCatalogue
) -> Callable[[IWriteGraphRepository], list[GraphSyncOutcome]]:
    """Reconciles the packaged set into the graphs it is handed — a new tenant's, on its creation's transaction."""

    def mint(graphs: IWriteGraphRepository) -> list[GraphSyncOutcome]:
        return GraphReconciliation(
            GraphMintService(graphs=graphs, clock=clock, catalogue=catalogue), graphs, yaml
        ).outcomes()

    return mint
