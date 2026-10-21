"""``blizzard runner chunk notes`` — a worker's read of every note a person left on its own chunk.

Lease-scoped and forwarded to the hub exactly as ``chunk asks`` is; the projection from the hub's
facts to note rows is this route's own."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.chunk_asks import ChunkNotesSource, ChunkNoteView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/notes", response_model=list[ChunkNoteView])
def get_chunk_notes(lease_id: str, request: Request) -> list[ChunkNoteView]:
    """Every note a person left on the worker's own chunk, oldest-first; a fact without one has no row."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "notes").get(f"/api/fleet/chunks/{lease.chunk_id}", chunk_id=lease.chunk_id)
    return _rows(ChunkNotesSource.model_validate(upstream.json()))


def _rows(source: ChunkNotesSource) -> list[ChunkNoteView]:
    rows = [
        *(
            ChunkNoteView(
                kind="resolution",
                note=r.note,
                by=r.resolved_by,
                node=r.node_name,
                choice=r.choice,
                recorded_at=r.resolved_at,
            )
            for r in source.resolutions
            if r.note
        ),
        *(
            ChunkNoteView(kind="requeue", note=q.note, by=q.requeued_by, recorded_at=q.requeued_at)
            for q in source.requeues
            if q.note
        ),
        *(
            ChunkNoteView(
                kind="restart",
                note=s.note,
                by=s.restarted_by,
                node=s.to_node_name or s.to_node_id,
                recorded_at=s.recorded_at,
            )
            for s in source.restarts
            if s.note
        ),
    ]
    return sorted(rows, key=lambda r: r.recorded_at)
