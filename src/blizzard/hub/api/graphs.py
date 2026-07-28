"""Graph routes — ``POST /api/graphs``, ``POST /api/graphs/sync``, ``GET /api/graphs``,
``GET /api/graphs/{id}``, ``POST /api/graphs/{id}/retire``, ``POST /api/graphs/{id}/enable``.

Mint a workflow graph from a YAML definition: parse it, validate (errors reject 422
with a :class:`GraphValidationReport`, warnings flag), reify immutable.
``GET /api/graphs`` lists every minted graph as a summary, newest first, with the
newest non-retired graph of each name marked ``effective`` — the domain's
:func:`~blizzard.hub.domain.graph.mark_effective` derives the marker, so the
"newest-per-name, retired-excluded" rule (issue #101) lives in one place.
``POST /api/graphs/sync`` reconciles the hub's own **packaged** graph set against the
store, minting only what changed (issue #146) — the reconciliation itself is
:mod:`blizzard.hub.graph_sync`'s, so this route is a thin adapter and the same function
serves an at-startup reconciliation unchanged. It is hub-side rather than a CLI-composed
loop because the packaged set that matters is the one the **running daemon** was deployed
with, which a possibly-different client wheel cannot speak for.
``GET /api/graphs/{graph_id}`` serves the full reified graph; unknown id resolves to
404 at the edge. ``retire``/``enable`` append a reversible lifecycle fact
(:class:`~blizzard.hub.domain.graph_lifecycle.GraphLifecycleService`) — the ``graphs``
row itself is never touched.
The controller stays read-only over the store (``bzh:controller-read-only``): it
resolves the YAML into a :class:`GraphDoc` and delegates the validate-reify-persist
to :class:`~blizzard.hub.domain.graph_authoring.GraphMintService`, and resolves a
``graph_id`` to its :class:`Graph` before delegating a retire/enable to
:class:`~blizzard.hub.domain.graph_lifecycle.GraphLifecycleService`
(``bzh:domain-takes-objects``).

``dependencies=[Depends(reject_runner_principal)]`` rejects a runner's bearer token
here rather than treating it as anonymous-plus-credential — a runner's token is
confined to the fleet router (issue #104; this router was the one operator router
still missing the check every other one already carries).
"""

from __future__ import annotations

from typing import Annotated

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import FLEET_VIEW, GRAPH_EDIT
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.graph import Graph, GraphParseError, Node, mark_effective, parse_graph_doc
from blizzard.hub.domain.graph_authoring import GraphValidationError
from blizzard.hub.graph_sync import GraphSyncStatus, reconcile_packaged_graphs
from blizzard.wire.graph import (
    GraphChoiceView,
    GraphEdgeView,
    GraphLifecycleRequest,
    GraphMintRequest,
    GraphNodeView,
    GraphPolicyRequest,
    GraphSessionView,
    GraphSummaryView,
    GraphSyncEntry,
    GraphSyncResponse,
    GraphValidationReport,
    GraphView,
    ProducesEntry,
    RotatePolicyView,
)

router = APIRouter(prefix="/api", tags=["graphs"], dependencies=[Depends(reject_runner_principal)])


def _node_view(node: Node) -> GraphNodeView:
    return GraphNodeView(
        node_id=node.node_id,
        name=node.name,
        executor=node.executor.value,
        session=node.session.value,
        session_source=node.session_source,
        judged_by=node.judged_by.value,
        retries_max=node.retries_max,
        retries_exhausted=node.retries_exhausted,
        mode=node.mode,
        prompt=node.prompt,
        checks=list(node.checks),
        checks_cwd=node.checks_cwd,
        checks_timeout=node.checks_timeout,
        produces=[ProducesEntry(name=p.name, kind=p.kind) for p in node.produces],
        judgement_prompt=node.judgement_prompt,
        choices=[
            GraphChoiceView(
                choice_id=c.choice_id, name=c.name, description=c.description, requires_checks=c.requires_checks
            )
            for c in node.choices
        ],
    )


def _graph_view(
    graph: Graph, *, retired: bool, follow_latest: bool | None = None, warnings: list[str] | None = None
) -> GraphView:
    return GraphView(
        graph_id=graph.graph_id,
        name=graph.name,
        entry_node_id=graph.entry_node_id,
        enabled=not retired,
        retired=retired,
        follow_latest=follow_latest,
        sessions=[
            GraphSessionView(
                name=s.name,
                model=list(s.model),
                effort=s.effort,
                rotate=RotatePolicyView(
                    max_context_tokens=s.rotate.max_context_tokens,
                    max_transcript_bytes=s.rotate.max_transcript_bytes,
                    max_invocations=s.rotate.max_invocations,
                )
                if s.rotate is not None
                else None,
            )
            for s in graph.sessions
        ],
        nodes=[_node_view(n) for n in graph.nodes],
        edges=[
            GraphEdgeView(
                from_node_id=e.from_node_id,
                choice_id=e.choice_id,
                to_node_name=e.to_node_name,
                prompt_addendum=e.prompt_addendum,
            )
            for e in graph.edges
        ],
        warnings=warnings or [],
    )


@router.post(
    "/graphs",
    response_model=GraphView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def mint_graph(request: GraphMintRequest, services: Annotated[HubServices, Depends(get_services)]) -> object:
    """Validate and mint an immutable graph; 422 on validation errors."""
    try:
        raw = yaml.safe_load(request.definition_yaml)
        if not isinstance(raw, dict):
            raise GraphParseError("graph definition must be a YAML mapping")
        doc = parse_graph_doc(raw)
    except (GraphParseError, yaml.YAMLError) as exc:
        report = GraphValidationReport(ok=False, errors=[str(exc)], warnings=[])
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=report.model_dump())

    try:
        graph, warnings = services.graph_mint.mint(doc, definition_yaml=request.definition_yaml)
    except GraphValidationError as exc:
        report = GraphValidationReport(ok=False, errors=exc.result.errors, warnings=exc.result.warnings)
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=report.model_dump())

    # A freshly minted graph carries no lifecycle fact yet — it starts enabled.
    return _graph_view(graph, retired=False, warnings=warnings)


@router.post("/graphs/sync", response_model=GraphSyncResponse, dependencies=[Depends(require(GRAPH_EDIT))])
def sync_graphs(services: Annotated[HubServices, Depends(get_services)]) -> GraphSyncResponse:
    """Reconcile the packaged graph set against the store, minting only what changed.

    Safe to run unconditionally at the end of every deploy (issue #146): a wheel whose
    packaged graphs all match the newest mint of their name mints nothing and churns no
    lineage, so re-running it is a no-op rather than a new generation of every graph.

    Registered **above** ``/graphs/{graph_id}``'s routes so ``sync`` is not swallowed as a
    graph id — FastAPI matches in declaration order, and the two would otherwise collide
    only for the one literal that matters.

    Always ``200``. A graph that fails to load or validate is a ``failed`` row in the
    report rather than a status code, because the other graphs still reconciled and the
    caller needs to see both halves; ``ok`` carries the pass/fail the CLI exits on.
    """
    outcomes = reconcile_packaged_graphs(services.graph_mint, services.graphs)
    return GraphSyncResponse(
        ok=all(o.status is not GraphSyncStatus.FAILED for o in outcomes),
        entries=[
            GraphSyncEntry(name=o.name, status=o.status.value, graph_id=o.graph_id, detail=o.detail) for o in outcomes
        ],
    )


@router.get("/graphs", response_model=list[GraphSummaryView], dependencies=[Depends(require(FLEET_VIEW))])
def list_graphs(services: Annotated[HubServices, Depends(get_services)]) -> list[GraphSummaryView]:
    """Every minted graph, newest first, newest non-retired per name marked ``effective``."""
    graphs = services.graphs.list_all()
    retired_ids = services.graphs.retired_graph_ids()
    effective_by_id = mark_effective(graphs, retired_ids=retired_ids)
    return [
        GraphSummaryView(
            graph_id=g.graph_id,
            name=g.name,
            entry_node_id=g.entry_node_id,
            created_at=iso_utc(g.created_at),
            effective=effective_by_id[g.graph_id],
            retired=g.graph_id in retired_ids,
        )
        for g in graphs
    ]


@router.get("/graphs/{graph_id}", response_model=GraphView, dependencies=[Depends(require(FLEET_VIEW))])
def get_graph(graph_id: str, services: Annotated[HubServices, Depends(get_services)]) -> GraphView:
    """One graph's full reified definition; 404 on unknown id."""
    graph = services.graphs.get(graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {graph_id}")
    return _graph_view(
        graph, retired=services.graphs.is_retired(graph_id), follow_latest=services.graphs.follow_latest(graph_id)
    )


@router.post(
    "/graphs/{graph_id}/retire",
    response_model=GraphView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def retire_graph(
    graph_id: str, request: GraphLifecycleRequest, services: Annotated[HubServices, Depends(get_services)]
) -> GraphView:
    """Retire a graph — excludes it from name resolution; the claim on any chunk
    already pinned to it runs on untouched (issue #101). 404 on an unknown id."""
    graph = services.graphs.get(graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {graph_id}")
    services.graph_lifecycle.retire(graph, by=request.by)
    return _graph_view(graph, retired=True, follow_latest=services.graphs.follow_latest(graph_id))


@router.post(
    "/graphs/{graph_id}/enable",
    response_model=GraphView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def enable_graph(
    graph_id: str, request: GraphLifecycleRequest, services: Annotated[HubServices, Depends(get_services)]
) -> GraphView:
    """Re-enable a retired graph — restores normal newest-per-name derivation
    (issue #101). Idempotent on an already-enabled graph; 404 on an unknown id."""
    graph = services.graphs.get(graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {graph_id}")
    services.graph_lifecycle.enable(graph, by=request.by)
    return _graph_view(graph, retired=False, follow_latest=services.graphs.follow_latest(graph_id))


@router.post(
    "/graphs/{graph_id}/follow-latest",
    response_model=GraphView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def set_graph_follow_latest(
    graph_id: str, request: GraphPolicyRequest, services: Annotated[HubServices, Depends(get_services)]
) -> GraphView:
    """Set this graph's follow-latest policy — ``true``/``false``/``null`` (issue #164).

    Appends a policy fact beside the retire/re-enable lifecycle rather than mutating the
    immutable ``graphs`` row. ``true``/``false`` override
    :attr:`~blizzard.hub.config.HubConfig.follow_latest` for chunks pinned to this mint;
    explicit ``null`` reverts to inheriting it, and is itself an appended fact, so
    clearing an override keeps the history. Idempotent, and 404 on an unknown id.

    Sets the policy on **one mint**, not on the name: a chunk consults the policy of the
    graph it is pinned to, so arming a lineage means arming the mint its chunks sit on
    (or the hub default, which covers every name at once)."""
    graph = services.graphs.get(graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {graph_id}")
    services.graph_lifecycle.set_follow_latest(graph, follow_latest=request.follow_latest, by=request.by)
    return _graph_view(
        graph, retired=services.graphs.is_retired(graph_id), follow_latest=services.graphs.follow_latest(graph_id)
    )
