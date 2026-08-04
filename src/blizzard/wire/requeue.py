"""``blizzard runner requeue`` — wire body (issue #53).

Behind ``POST /chunks/{id}/requeues``.
"""

from __future__ import annotations

from pydantic import BaseModel


class RequeueResponse(BaseModel):
    """``POST /chunks/{id}/requeues`` — the local hold is cleared."""

    chunk_id: str
    requeued: bool
