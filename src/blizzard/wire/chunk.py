"""Chunk ingest, views, and the PM pass-through (D-047/D-004).

Ingest wraps one or more PM pointers into chunks (``POST /chunks``); a pointer
already held by a live chunk is rejected **409** with the existing chunk id (D-093).
The list/detail views carry the **derived** status (D-004) — never a stored column
— and the current node. ``GET /chunks/{id}/pm-item`` is the vendor-native
pass-through read (D-047), contents never stored.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.hub.domain.work import ChunkStatus


class PmPointerModel(BaseModel):
    """One ``{provider, url}`` PM pointer (D-075)."""

    provider: str
    url: str


class ChunkIngestRequest(BaseModel):
    """Ingest by pointer — specific items always, batch fine (D-047)."""

    pointers: list[PmPointerModel]


class ChunkIngestResponse(BaseModel):
    """The minted chunk id."""

    chunk_id: str


class ChunkIngestConflict(BaseModel):
    """The 409 body: the pointer is already held by a live chunk (D-093)."""

    existing_chunk_id: str
    provider: str
    url: str
    detail: str = "pointer already held by a live chunk"


class ChunkSummary(BaseModel):
    """One row of the fleet chunk list — derived status + current node (D-004)."""

    chunk_id: str
    graph_id: str
    status: ChunkStatus
    current_node_id: str | None
    pm_pointers: list[PmPointerModel] = []


class RouteView(BaseModel):
    """A chunk's route — where it is being worked (D-021)."""

    runner_id: str
    workspace_id: str
    environment_ids: list[str] = []


class EscalationView(BaseModel):
    """An open escalation on a ``needs_human`` chunk (D-009/D-067).

    Surfaces the runner-composed takeover command so a human can resume the parked
    session (design/harness-adapters.md). Present only while the escalation is open —
    a later lease mint (requeue/takeover) supersedes it and this drops away (D-067)."""

    epoch: int
    takeover_command: str


class ChunkDetail(BaseModel):
    """The chunk aggregate in full (D-036) — the board's chunk view and envelope feed."""

    chunk_id: str
    graph_id: str
    status: ChunkStatus
    current_node_id: str | None
    latest_epoch: int | None
    pm_pointers: list[PmPointerModel] = []
    route: RouteView | None = None
    escalation: EscalationView | None = None


class PmItemView(BaseModel):
    """A pass-through PM item read (D-047) — body + comments, vendor-native."""

    provider: str
    url: str
    fetched_at: str
    body: str
    comments: list[str] = []
