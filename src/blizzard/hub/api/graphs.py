"""Graph routes — ``POST /api/graphs`` (D-071).

Mint a workflow graph from a YAML definition: parse it, validate (errors reject 422
with a :class:`GraphValidationReport`, warnings flag — D-071), reify immutable (D-033).
The controller stays read-only over the store (``bzh:controller-read-only``): it
resolves the YAML into a :class:`GraphDoc` and delegates the validate-reify-persist
to :class:`~blizzard.hub.domain.graph_authoring.GraphMintService`.
"""

from __future__ import annotations

from typing import Annotated

import yaml
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.graph import GraphParseError, parse_graph_doc
from blizzard.hub.domain.graph_authoring import GraphValidationError
from blizzard.wire.graph import GraphMintRequest, GraphNodeView, GraphValidationReport, GraphView

router = APIRouter(prefix="/api", tags=["graphs"])


@router.post("/graphs", response_model=GraphView, status_code=status.HTTP_201_CREATED)
def mint_graph(request: GraphMintRequest, services: Annotated[HubServices, Depends(get_services)]) -> object:
    """Validate and mint an immutable graph; 422 on validation errors (D-071)."""
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

    return GraphView(
        graph_id=graph.graph_id,
        name=graph.name,
        entry_node_id=graph.entry_node_id,
        enabled=True,
        nodes=[
            GraphNodeView(node_id=n.node_id, name=n.name, executor=n.executor.value, judged_by=n.judged_by.value)
            for n in graph.nodes
        ],
        warnings=warnings,
    )
