"""``blizzard runner artifact create`` — ``POST /api/leases/{lease_id}/attachments``
(issue #113), plus its read-back counterpart ``GET`` (issue #169).

The lease token is presented as ``X-Blizzard-Lease-Token`` or ``Authorization: Bearer``,
the dedicated header checked first. ``404`` unknown/closed lease, ``403`` bad token."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.attachments import AttachmentRejected
from blizzard.runner.stores import IReadRunnerStore
from blizzard.wire.attachments import AttachmentRequest, AttachmentResponse, StagedAttachment

router = APIRouter(prefix="/api", tags=["runner"])


@router.post("/leases/{lease_id}/attachments", response_model=AttachmentResponse, status_code=status.HTTP_200_OK)
def record_attachment(lease_id: str, request_body: AttachmentRequest, request: Request) -> AttachmentResponse:
    """Record a worker's explicit artifact for ``request_body.name`` against its lease."""
    service = RunnerWiring.of(request).attachments()
    lease = RunnerWiring.of(request).worker_lease(lease_id)
    try:
        service.attach(
            lease,
            presented_token=presented_lease_token(request),
            name=request_body.name,
            content=request_body.content,
        )
    except AttachmentRejected as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return AttachmentResponse(
        recorded=True,
        lease_id=lease_id,
        name=request_body.name,
        bytes=len(request_body.content.encode("utf-8")),
    )


@router.get("/leases/{lease_id}/attachments", response_model=list[StagedAttachment])
def list_staged_attachments(lease_id: str, request: Request) -> list[StagedAttachment]:
    """The lease's currently staged submissions — newest content per ``name``, not yet
    published into any envelope (issue #169)."""
    lease = authorized_lease(lease_id, request)
    store: IReadRunnerStore = request.app.state.runner_store
    staged = store.attachments_for_lease(lease.lease_id)
    return [StagedAttachment(name=name, content=content) for name, content in sorted(staged.items())]
