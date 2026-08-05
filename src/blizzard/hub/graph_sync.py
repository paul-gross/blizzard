"""Reconcile the packaged graph set against the store — ``blizzard hub graph sync`` (#146).

The edge half: walk the packaged set, load and inline each ``graph.yaml`` (filesystem and
PyYAML, both outside the domain — ``bzh:domain-core``), and hand it with the stored definition
to the domain, which owns the mint-only-if-changed rule. Per-graph isolated — one graph
failing to load is a report row, not a stop — and additive: nothing re-pins a chunk."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.graph import GraphDoc, GraphParseError, IReadGraphRepository, parse_graph_doc
from blizzard.hub.domain.graph_authoring import GraphMintService, GraphValidationError
from blizzard.hub.graphs import inline_graph_yaml, load_graph_doc, packaged_graph_paths

_log = get_logger("blizzard.hub.graph_sync")


class GraphSyncStatus(StrEnum):
    """What reconciliation did with one packaged graph."""

    MINTED = "minted"
    UP_TO_DATE = "up-to-date"
    FAILED = "failed"


@dataclass(frozen=True)
class GraphSyncOutcome:
    """One packaged graph's reconciliation result — a row of the report.

    ``name`` is the authored name where one could be read, else the packaged directory
    name. ``detail`` explains a mint or a failure, and is ``None`` for up-to-date."""

    name: str
    status: GraphSyncStatus
    graph_id: str | None = None
    detail: str | None = None


def reconcile_packaged_graphs(
    mint_service: GraphMintService, graphs: IReadGraphRepository, *, paths: list[Path] | None = None
) -> list[GraphSyncOutcome]:
    """Reconcile every packaged graph against the store; mint only what changed.

    Idempotent: a second run against an unchanged packaged set mints nothing. ``paths``
    overrides the packaged set, defaulting to
    :func:`~blizzard.hub.graphs.packaged_graph_paths`."""
    return [
        _reconcile_one(mint_service, graphs, path) for path in (paths if paths is not None else packaged_graph_paths())
    ]


def _reconcile_one(mint_service: GraphMintService, graphs: IReadGraphRepository, path: Path) -> GraphSyncOutcome:
    """One packaged graph, with every failure mode folded into a report row.

    The load and the compare are separate ``try`` blocks: a graph that cannot be loaded
    has no authored name to report a validation failure under."""
    try:
        doc = load_graph_doc(path)
        definition_yaml = inline_graph_yaml(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        # ValueError covers GraphParseError (its base) and the loader's "not a mapping".
        _log.warning("packaged graph failed to load", path=str(path), error=str(exc))
        return GraphSyncOutcome(name=path.parent.name, status=GraphSyncStatus.FAILED, detail=str(exc))

    stored = graphs.newest_definition_yaml(doc.name)
    try:
        graph = mint_service.mint_if_changed(
            doc, definition_yaml=definition_yaml, minted=_parse_stored(stored, doc.name)
        )
    except GraphValidationError as exc:
        _log.warning("packaged graph failed validation", graph=doc.name, error=str(exc))
        return GraphSyncOutcome(name=doc.name, status=GraphSyncStatus.FAILED, detail="; ".join(exc.result.errors))

    if graph is None:
        return GraphSyncOutcome(name=doc.name, status=GraphSyncStatus.UP_TO_DATE)
    reason = "first of its name" if stored is None else "packaged definition differs from the newest mint"
    _log.info("graph minted by reconciliation", graph=doc.name, graph_id=graph.graph_id, reason=reason)
    return GraphSyncOutcome(name=doc.name, status=GraphSyncStatus.MINTED, graph_id=graph.graph_id, detail=reason)


def _parse_stored(stored: str | None, name: str) -> GraphDoc | None:
    """A stored definition re-parsed into an authoring doc, or ``None``.

    ``None`` means "nothing to compare against", which mints. It covers both the
    never-minted name and the stored definition that no longer parses — that one stays in
    the store, superseded rather than lost."""
    if stored is None:
        return None
    try:
        raw = yaml.safe_load(stored)
        return parse_graph_doc(raw) if isinstance(raw, dict) else None
    except (yaml.YAMLError, GraphParseError):
        _log.warning("stored graph definition no longer parses; treating it as changed", graph=name)
        return None
