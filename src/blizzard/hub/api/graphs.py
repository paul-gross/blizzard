"""Graph routes — mint, sync, list, read, retire, and enable a workflow graph.

The controller stays read-only over the store (``bzh:controller-read-only``), resolving a
YAML body or a ``graph_id`` into an object before delegating to the domain
(``bzh:domain-takes-objects``). ``reject_runner_principal`` confines a runner's bearer
token to the fleet router (issue #104)."""

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
from blizzard.hub.domain.graph import Graph, GraphDoc, GraphParseError, Mints, Node
from blizzard.hub.domain.graph_authoring import GraphValidationError
from blizzard.hub.graph_sync import GraphReconciliation, GraphSyncStatus
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
        doc = GraphDoc.of(raw)
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

    Idempotent, so it is safe to run unconditionally (issue #146). Registered above
    ``/graphs/{graph_id}`` so ``sync`` is not matched as a graph id. Always ``200``: a
    graph that fails to load is a ``failed`` report row, and ``ok`` carries the verdict."""
    outcomes = GraphReconciliation(services.graph_mint, services.graphs).outcomes()
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
    effective_by_id = Mints.of(graphs, retired_ids=retired_ids).effective
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

    Appends a policy fact rather than mutating the immutable ``graphs`` row; explicit
    ``null`` reverts to inheriting the hub default and is itself an appended fact. Scoped
    to this one mint, not to the graph name. Idempotent, and 404 on an unknown id."""
    graph = services.graphs.get(graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {graph_id}")
    services.graph_lifecycle.set_follow_latest(graph, follow_latest=request.follow_latest, by=request.by)
    return _graph_view(
        graph, retired=services.graphs.is_retired(graph_id), follow_latest=services.graphs.follow_latest(graph_id)
    )
