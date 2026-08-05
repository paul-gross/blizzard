"""Wire bodies for a worker's own artifact submissions (issues #113, #169):
``AttachmentRequest``/``AttachmentResponse`` for the write, and ``StagedAttachment`` for the read-back
of a node-step's staged, not-yet-published submissions."""

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
