"""Reconcile the packaged graph set against the store — ``blizzard hub graph sync`` (#146).

The gap this closes. Graphs live in the hub's **store**, not on disk: the hub resolves a
minted graph per chunk and never re-reads the packaged YAML under
:mod:`blizzard.hub.graphs`. Minting was an operator verb only, and nothing minted at
startup — so shipping a changed graph in a new wheel did not change fleet behavior. The
deploy succeeded, the daemons came up healthy, and every new chunk kept running the
previous definition, with no error, log line, or status output saying so. It cost a real
deploy: ``bas-dwf`` gained a ``retrospective`` node, the wheel was built, installed,
migrated and restarted with every check green, and the running hub went on serving the
four-node lane. The drift was caught only by hand-diffing ``hub graph show`` against the
source.

This module is the **edge half** of the fix: it walks the packaged set, loads and inlines
each ``graph.yaml`` (filesystem + PyYAML, both outside the domain — ``bzh:domain-core``),
re-parses what the store already holds, and hands both to
:meth:`~blizzard.hub.domain.graph_authoring.GraphMintService.mint_if_changed`, which owns
the mint-only-if-changed rule itself. It is deliberately not a route body: the same
function is what an at-startup reconciliation would call, so the two delivery shapes the
issue weighs share one implementation rather than two that must agree.

**Per-graph isolation.** One packaged graph failing to load, parse, or validate must not
stop the others reconciling — a wheel that ships one bad graph should still converge the
rest and say plainly which one it could not. Every outcome, good or bad, is reported;
only a :attr:`GraphSyncStatus.FAILED` row makes the caller's exit non-zero.

**Additive, never re-pinning.** A mint appends a new definition and supersedes the prior
one for *future* resolution; a chunk in flight stays on the definition it started under,
because a chunk pins its graph by id at mint (``chunks.graph_id``) and nothing here
touches a chunk. Deliberate migration of in-flight work is issue #124's standing intent
and #164's follow-latest policy, not this.
"""

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

    ``name`` is the graph's authored name where one could be read, else the packaged
    directory name: a graph that fails to *parse* has no authored name, and a report row
    with no way to name the file it came from would be useless for the one job it has.
    ``detail`` explains a :attr:`GraphSyncStatus.MINTED` ("why") or a
    :attr:`GraphSyncStatus.FAILED` (the error); ``None`` for up-to-date, which needs none.
    """

    name: str
    status: GraphSyncStatus
    graph_id: str | None = None
    detail: str | None = None


def reconcile_packaged_graphs(
    mint_service: GraphMintService, graphs: IReadGraphRepository, *, paths: list[Path] | None = None
) -> list[GraphSyncOutcome]:
    """Reconcile every packaged graph against the store; mint only what changed.

    Idempotent: run twice against an unchanged wheel and the second run mints nothing, so
    a deploy can call it unconditionally without churning graph lineage. ``paths``
    overrides the packaged set (tests point it at a fixture directory); it defaults to
    :func:`~blizzard.hub.graphs.packaged_graph_paths`.
    """
    return [
        _reconcile_one(mint_service, graphs, path) for path in (paths if paths is not None else packaged_graph_paths())
    ]


def _reconcile_one(mint_service: GraphMintService, graphs: IReadGraphRepository, path: Path) -> GraphSyncOutcome:
    """One packaged graph, with every failure mode folded into a report row.

    The load and the compare are separate ``try`` blocks on purpose. A packaged graph that
    cannot be loaded has no name to reconcile *against*, so it fails immediately; once
    loaded, a validation error is reported under the graph's own authored name, which is
    what an operator reading the report needs to find it.
    """
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

    ``None`` means "nothing to compare against", which mints. That covers the name that
    was never minted *and* the stored definition that no longer parses (an older
    authoring schema the current parser cannot express) — the reconciler mints the
    packaged graph rather than wedging on a definition nobody can compare against, and the
    unparseable one stays in the store, superseded rather than lost. The two are told
    apart by the caller for the report's ``detail``, from ``stored`` itself.
    """
    if stored is None:
        return None
    try:
        raw = yaml.safe_load(stored)
        return parse_graph_doc(raw) if isinstance(raw, dict) else None
    except (yaml.YAMLError, GraphParseError):
        _log.warning("stored graph definition no longer parses; treating it as changed", graph=name)
        return None
