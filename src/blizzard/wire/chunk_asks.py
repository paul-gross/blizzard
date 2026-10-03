"""The worker-facing chunk-asks wire shapes.

``ChunkAskView`` is the row a later session reads for a question asked on its chunk, with its
answer. The projection from the hub's ``QuestionView`` is not here: a wire model declares shape
only, never a projection into another model."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.chunk import MigrationView, TransitionView
from blizzard.wire.question import QuestionView


class ChunkAskView(BaseModel):
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
