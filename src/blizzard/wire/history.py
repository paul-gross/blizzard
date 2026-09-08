"""The worker-facing chunk-history wire shapes (issue #237).

``HistoryRowView`` is a flat, kind-discriminated row — ``transition`` | ``migration`` |
``bounce`` — merged oldest-first across a chunk's three hub-side histories. The merge
itself is ``runner/api/history.py``'s: a wire model declares shape only, never a
projection into another model (D3, plan: hold wire/ to its stated contract)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from blizzard.wire.chunk import BounceView, MigrationView, TransitionView


class HistoryRowView(BaseModel):
    """One row of a chunk's own timeline — a transition, a cross-graph migration, or a
    delivery bounce, merged oldest-first by ``recorded_at``. ``from_node``/``to_node`` are
    node labels (a migration's is a ``graph/node`` hop), both null for a bounce; ``epoch``
    is transition-only; ``cause``/``detail`` carry a bounce's kick-back or a migration source."""

    kind: Literal["transition", "migration", "bounce"]
    from_node: str | None = None
    to_node: str | None = None
    choice: str | None = None
    epoch: int | None = None
    graph_name: str | None = None
    cause: str | None = None
    detail: str | None = None
    recorded_at: str


class ChunkHistoryView(BaseModel):
    """The history/migrations/bounces slice of a hub ``ChunkDetail`` payload — never a FastAPI
    ``response_model``, decoded with pydantic's default ``extra="ignore"``. The three fields
    are **required**, not defaulted to ``[]`` (issue #237), so a rename fails loudly rather
    than decoding as "no history yet"."""

    history: list[TransitionView]
    migrations: list[MigrationView]
    bounces: list[BounceView]
