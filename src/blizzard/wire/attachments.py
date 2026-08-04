"""``blizzard runner artifact create`` (née ``attach``) — wire body (issue #113, Phase 2;
issue #169).

``AttachmentRequest``/``AttachmentResponse`` are behind ``POST
/api/leases/{lease_id}/attachments``.
``StagedAttachment`` is behind the read counterpart, ``GET
/api/leases/{lease_id}/attachments`` — a worker's read-back of its own node-step's
staged (not-yet-published) submissions.
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
    bytes: int


class StagedAttachment(BaseModel):
    """One of the lease's currently staged (not-yet-published) submissions —
    ``GET /api/leases/{lease_id}/attachments`` (issue #169)."""

    name: str
    content: str
