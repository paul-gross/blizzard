"""The ready-queue peek (D-080) — the read a runner's FILL step does before a claim.

``GET /queue/peek`` returns the hub-ordered ready queue (chunks with no live route),
read-only. FILL peeks it, acquires environments for a candidate, then claims via
``POST /routes`` (D-080). The ordering mechanism is the queue-ordering open
question; order derives from appended facts (D-004).
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.chunk import PmPointerModel


class QueuePeekEntry(BaseModel):
    """One ready chunk, in queue order."""

    chunk_id: str
    graph_id: str
    position: int
    pm_pointers: list[PmPointerModel] = []


class QueuePeekResponse(BaseModel):
    """The ready queue as peeked by FILL."""

    entries: list[QueuePeekEntry] = []
