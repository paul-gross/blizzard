"""The kinds of note a person leaves on a chunk."""

from __future__ import annotations

from typing import Literal

#: What a note a person left on a chunk was left for.
ChunkNoteKind = Literal["resolution", "requeue", "restart"]
