"""The worker-facing chunk-history wire shapes: ``HistoryRowView``, one kind-discriminated row of a chunk's
timeline, and ``ChunkHistoryView``, the history slice of a chunk's detail."""

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
    """The history slice of a chunk's detail: its transitions, migrations, bounces, resolutions, requeues, and
    restarts. The first three are required; the other three are empty when absent."""

    history: list[TransitionView]
    migrations: list[MigrationView]
    bounces: list[BounceView]
    resolutions: list[ResolutionView] = []
    requeues: list[RequeueView] = []
    restarts: list[RestartView] = []
