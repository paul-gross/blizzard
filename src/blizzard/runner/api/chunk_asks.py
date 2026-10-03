"""``blizzard runner chunk asks`` — a worker's read of every question asked on its own chunk,
with the answers people gave.

Lease-scoped and token-authorized, then forwarded to the hub as the runner principal.
``503`` unwired, ``404`` unknown/closed lease, ``403`` bad token, ``502`` on a failed
forward; authorization resolves before the hub is consulted. The projection from the hub's
questions to worker rows is this route's own."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.chunk_asks import ChunkAsksSource, ChunkAskView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/asks", response_model=list[ChunkAskView])
def get_chunk_asks(lease_id: str, request: Request) -> list[ChunkAskView]:
    """Every question asked on the worker's own chunk, oldest-first — open, answered, and
    restart-superseded alike. An answer a person gave binds every later session on the chunk."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "asks").get(f"/api/fleet/chunks/{lease.chunk_id}", chunk_id=lease.chunk_id)
    return _rows(ChunkAsksSource.model_validate(upstream.json()))


def _node_names(source: ChunkAsksSource) -> dict[str, str]:
    """Node id -> name, from every id/name pair the payload carries."""
    pairs: list[tuple[str | None, str | None]] = [(source.current_node_id, source.current_node_name)]
    for t in source.history:
        pairs += [(t.from_node_id, t.from_node_name), (t.to_node_id, t.to_node_name)]
    for m in source.migrations:
        pairs += [(m.from_node_id, m.from_node_name), (m.landed_node_id, m.landed_node_name)]
    return {node_id: name for node_id, name in pairs if node_id and name}


def _rows(source: ChunkAsksSource) -> list[ChunkAskView]:
    names = _node_names(source)
    return [
        ChunkAskView(
            question_id=q.question_id,
            node=names.get(q.node_id, q.node_id) if q.node_id else None,
            epoch=q.epoch,
            question=q.question,
            options=q.options,
            asked_at=q.asked_at,
            answered=q.answered,
            answer=q.answer,
            answered_by=q.answered_by,
            answered_at=q.answered_at,
        )
        for q in sorted(source.questions, key=lambda q: q.asked_at)
    ]
