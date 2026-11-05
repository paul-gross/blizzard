"""Wire bodies for a worker's own artifact submissions:
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
    ``GET /api/leases/{lease_id}/attachments``."""

    name: str
    content: str


class ArtifactCheckRequest(BaseModel):
    """``POST /api/fleet/chunks/{chunk_id}/artifact-check`` — content a worker is about to stage under ``name``."""

    name: str
    content: str


class ArtifactCheckResponse(BaseModel):
    """The hub's verdict on a staging offer: ``refusal`` is the delivery-time message when the content fails
    the format its graph owes ``name``, and absent otherwise."""

    refusal: str | None = None
