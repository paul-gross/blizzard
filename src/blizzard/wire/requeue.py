"""``blizzard runner requeue`` — wire body (issue #53).

Behind ``POST /chunks/{id}/requeues``. Modeled on ``wire/takeover.py``'s shapes.
"""

from __future__ import annotations

from pydantic import BaseModel


class RequeueResponse(BaseModel):
    """``POST /chunks/{id}/requeues`` — the local hold is cleared; the next FILL spawns
    a fresh attempt at the chunk's current node."""

    chunk_id: str
    requeued: bool
