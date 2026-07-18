"""Graph routes — ``POST /api/graphs``, ``GET /api/graphs``, ``GET /api/graphs/{id}``.

Mint a workflow graph from a YAML definition: parse it, validate (errors reject 422
with a :class:`GraphValidationReport`, warnings flag), reify immutable.
``GET /api/graphs`` lists every minted graph as a summary, newest first, with the
newest graph of each name marked ``effective`` — the domain's
:func:`~blizzard.hub.domain.graph.mark_effective` derives the marker, so the
"newest-per-name" rule lives in one place. ``GET /api/graphs/{graph_id}`` serves
the full reified graph; unknown id resolves to 404 at the edge.
The controller stays read-only over the store (``bzh:controller-read-only``): it
resolves the YAML into a :class:`GraphDoc` and delegates the validate-reify-persist
to :class:`~blizzard.hub.domain.graph_authoring.GraphMintService`.
"""

from __future__ import annotations

from typing import Annotated

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.graph import Graph, GraphParseError, Node, mark_effective, parse_graph_doc
from blizzard.hub.domain.graph_authoring import GraphValidationError
from blizzard.wire.graph import (
    GraphChoiceView,
    GraphEdgeView,
    GraphMintRequest,
    GraphNodeView,
    GraphSummaryView,
    GraphValidationReport,
    GraphView,
)

router = APIRouter(prefix="/api", tags=["graphs"])


def _node_view(node: Node) -> GraphNodeView:
    return GraphNodeView(
        node_id=node.node_id,
        name=node.name,
        executor=node.executor.value,
        session=node.session.value,
        judged_by=node.judged_by.value,
        retries_max=node.retries_max,
        retries_exhausted=node.retries_exhausted,
        mode=node.mode,
        prompt=node.prompt,
        checks=list(node.checks),
        produces=list(node.produces),
        judgement_prompt=node.judgement_prompt,
        choices=[GraphChoiceView(choice_id=c.choice_id, name=c.name, description=c.description) for c in node.choices],
    )


def _graph_view(graph: Graph, *, enabled: bool, warnings: list[str] | None = None) -> GraphView:
    return GraphView(
        graph_id=graph.graph_id,
        name=graph.name,
        entry_node_id=graph.entry_node_id,
        enabled=enabled,
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


@router.post("/graphs", response_model=GraphView, status_code=status.HTTP_201_CREATED)
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

    return _graph_view(graph, enabled=True, warnings=warnings)


@router.get("/graphs", response_model=list[GraphSummaryView])
def list_graphs(services: Annotated[HubServices, Depends(get_services)]) -> list[GraphSummaryView]:
    """Every minted graph, newest first, newest per name marked ``effective``."""
    graphs = services.graphs.list_all()
    effective_by_id = mark_effective(graphs)
    return [
        GraphSummaryView(
            graph_id=g.graph_id,
            name=g.name,
            entry_node_id=g.entry_node_id,
            created_at=iso_utc(g.created_at),
            effective=effective_by_id[g.graph_id],
        )
        for g in graphs
    ]


@router.get("/graphs/{graph_id}", response_model=GraphView)
def get_graph(graph_id: str, services: Annotated[HubServices, Depends(get_services)]) -> GraphView:
    """One graph's full reified definition; 404 on unknown id."""
    graph = services.graphs.get(graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {graph_id}")
    return _graph_view(graph, enabled=True)
