"""Graph routes — ``POST /api/graphs`` (D-071).

Mint a workflow graph from a YAML definition: validate (errors reject 422, warnings
flag — D-071), inline every file reference, mint immutable (D-033). The route is a
501 stub here — the walking-skeleton hub-track builder wires it to
:func:`blizzard.hub.domain.graph_validation.validate_graph` and a mint domain
service. The controller stays read-only over the store (``bzh:controller-read-only``):
minting flows through the domain, never a write repository held at the edge.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from blizzard.wire.graph import GraphMintRequest, GraphView

router = APIRouter(prefix="/api", tags=["graphs"])

_NOT_IMPLEMENTED = "graph mint lands in the P6 walking skeleton"


@router.post("/graphs", response_model=GraphView, status_code=status.HTTP_201_CREATED)
def mint_graph(request: GraphMintRequest) -> GraphView:
    """Validate and mint an immutable graph; 422 on validation errors (D-071)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)
