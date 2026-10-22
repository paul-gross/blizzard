"""The worker-facing chunk-asks wire shapes.

``ChunkAskView`` is the row a later session reads for a question asked on its chunk, with its
answer; ``ChunkNoteView`` is the row it reads for a note a person left resolving, requeueing or
restarting it."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel

from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.chunk import MigrationView, RequeueView, ResolutionView, RestartView, TransitionView
from blizzard.wire.question import QuestionView
from blizzard.wire.vocabulary import OPEN


class ChunkAskView(WireModel):
    """One question asked on a chunk, open or answered. ``node`` is the asking node's name,
    falling back to its raw id when the payload names no node by that id."""

    question_id: str
    node: str | None = None
    epoch: int
    question: str
    options: list[str] = []
    asked_at: str
    answered: bool = False
    answer: str | None = None
    answered_by: str | None = None
    answered_at: str | None = None


class ChunkAsksSource(BaseModel):
    """The questions slice of a hub ``ChunkDetail`` payload, plus the node-name sources — never a
    FastAPI ``response_model``, decoded with pydantic's default ``extra="ignore"``. ``questions``
    is **required**, so a rename fails loudly rather than decoding as "nothing asked"; the
    name sources are defaulted so a detail without history still decodes."""

    questions: list[QuestionView]
    history: list[TransitionView] = []
    migrations: list[MigrationView] = []
    current_node_id: str | None = None
    current_node_name: str | None = None


class ChunkNoteView(WireModel):
    """One note a person left on a chunk. ``node`` and ``choice`` are the resolved gate's for a
    resolution and the landing node for a restart; a requeue has neither."""

    kind: Annotated[Literal["resolution", "requeue", "restart"] | Unrecognized, OPEN]
    note: str
    by: str | None = None
    node: str | None = None
    choice: str | None = None
    recorded_at: str


class ChunkNotesSource(BaseModel):
    """The resolutions, requeues and restarts slice of a hub ``ChunkDetail`` payload — never a
    response model, and each list defaulted as the hub's own detail defaults it."""

    resolutions: list[ResolutionView] = []
    requeues: list[RequeueView] = []
    restarts: list[RestartView] = []
