"""The worker-facing chunk-history wire shapes.

``HistoryRowView`` is a flat, kind-discriminated row merged oldest-first across a chunk's
hub-side histories. The merge
itself is not here: a wire model declares shape only, never a projection into another
model."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel

from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.chunk import BounceView, MigrationView, RequeueView, ResolutionView, RestartView, TransitionView
from blizzard.wire.vocabulary import OPEN


class HistoryRowView(WireModel):
    """One row of a chunk's own timeline. ``from_node``/``to_node`` are node labels (a migration's
    is a ``graph/node`` hop); ``cause``/``detail`` carry a bounce's kick-back or a migration
    source; ``by``/``note`` are the person behind a resolution, requeue or restart, and their note.
    ``kind`` is ``transition``, ``migration``, ``bounce``, ``resolution``, ``requeue`` or ``restart``."""

    kind: Annotated[
        Literal["transition", "migration", "bounce", "resolution", "requeue", "restart"] | Unrecognized, OPEN
    ]
    from_node: str | None = None
    to_node: str | None = None
    choice: str | None = None
    epoch: int | None = None
    graph_name: str | None = None
    cause: str | None = None
    detail: str | None = None
    by: str | None = None
    note: str | None = None
    recorded_at: str


class ChunkHistoryView(BaseModel):
    """The history/migrations/bounces slice of a hub ``ChunkDetail`` payload — never a FastAPI
    ``response_model``, decoded with pydantic's default ``extra="ignore"``. The first three fields
    are **required**, not defaulted to ``[]``, so a rename fails loudly rather than decoding as
    "no history yet"; the person-made lists are defaulted, so a hub without them still decodes."""

    history: list[TransitionView]
    migrations: list[MigrationView]
    bounces: list[BounceView]
    resolutions: list[ResolutionView] = []
    requeues: list[RequeueView] = []
    restarts: list[RestartView] = []
