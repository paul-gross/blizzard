"""``blizzard runner artifact create`` — ``POST /api/leases/{lease_id}/attachments``
plus its read-back counterpart ``GET``.

Token presentation is owned by ``lease_token.py``. ``404`` unknown/closed lease, ``403`` bad
token, ``409`` an open takeover's closed reference lease, ``422`` empty content."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.runner.api.lease_scope import authorized_lease, authorized_worker_lease
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.operator.attachments import AttachmentEmpty, AttachmentOnClosedLease
from blizzard.wire.attachments import AttachmentRequest, AttachmentResponse, StagedAttachment

router = APIRouter(prefix="/api", tags=["runner"])


@router.post("/leases/{lease_id}/attachments", response_model=AttachmentResponse, status_code=status.HTTP_200_OK)
def record_attachment(lease_id: str, request_body: AttachmentRequest, request: Request) -> AttachmentResponse:
    """Record a worker's explicit artifact for ``request_body.name`` against its lease."""
    service = RunnerWiring.of(request).attachments()
    worker = authorized_worker_lease(lease_id, request)
    try:
        service.attach(worker, name=request_body.name, content=request_body.content)
    except AttachmentEmpty as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except AttachmentOnClosedLease as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AttachmentResponse(
        recorded=True,
        lease_id=lease_id,
        name=request_body.name,
        bytes=len(request_body.content.encode("utf-8")),
    )


@router.get("/leases/{lease_id}/attachments", response_model=list[StagedAttachment])
def list_staged_attachments(lease_id: str, request: Request) -> list[StagedAttachment]:
    """The lease's currently staged submissions — newest content per ``name``, not yet
    published into any envelope."""
    lease = authorized_lease(lease_id, request)
    attachments = RunnerWiring.of(request).read_stores().attachments
    staged = attachments.attachments_for_lease(lease.lease_id)
    return [StagedAttachment(name=name, content=content) for name, content in sorted(staged.items())]
