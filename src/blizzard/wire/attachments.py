"""``blizzard runner attach`` — wire body (issue #113, Phase 2).

Behind ``POST /api/leases/{lease_id}/attachments``. Modeled on
``wire/requeue.py``'s shapes.
"""

from __future__ import annotations

from pydantic import BaseModel


class AttachmentRequest(BaseModel):
    """A worker's explicit artifact submission for one ``produces:`` name."""

    name: str
    content: str


class AttachmentResponse(BaseModel):
    """``POST /api/leases/{lease_id}/attachments`` — the submission landed durably."""

    recorded: bool
    lease_id: str
    name: str
