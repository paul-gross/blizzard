"""Requeue wire body: the response to clearing a chunk's local hold."""

from __future__ import annotations

from pydantic import BaseModel


class RequeueResponse(BaseModel):
    """``POST /chunks/{id}/requeues`` — the local hold is cleared."""

    chunk_id: str
    requeued: bool
