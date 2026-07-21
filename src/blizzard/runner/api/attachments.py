"""``blizzard runner attach`` — ``POST /api/leases/{lease_id}/attachments`` (issue #113,
Phase 2).

The CLI is a pure client of this one route: a worker durably submits an explicit
artifact for a ``produces:`` name, authorized by the lease token it inherited at spawn
(``BLIZZARD_LEASE_TOKEN``, Phase 1). Read-only over its wiring
(``bzh:controller-read-only``): the edge resolves the lease to an object through the
read-only store already on ``app.state`` (the same one ``asks.py``'s list route reads
through) and delegates the write to the composition-root-wired
:class:`~blizzard.runner.domain.attachments.AttachmentService` — it holds no write
repository of its own. The token is presented as ``X-Blizzard-Lease-Token`` or a
standard ``Authorization: Bearer`` header; either is accepted, the dedicated header
checked first.

``503`` when the store or the attachment service is unwired (the store-free app);
``404`` for an unknown or already-closed lease; ``403`` for a missing or mismatched
token; ``200`` on a recorded attach.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.runner.api.lease_token import presented_lease_token
from blizzard.runner.domain.attachments import AttachmentRejected, AttachmentService
from blizzard.runner.store.repository import IReadRunnerStore
from blizzard.wire.attachments import AttachmentRequest, AttachmentResponse

router = APIRouter(prefix="/api", tags=["runner"])


def _service(request: Request) -> AttachmentService:
    service: AttachmentService | None = getattr(request.app.state, "attachments", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="attachment service not wired — start via `blizzard runner host`",
        )
    return service


@router.post("/leases/{lease_id}/attachments", response_model=AttachmentResponse, status_code=status.HTTP_200_OK)
def record_attachment(lease_id: str, request_body: AttachmentRequest, request: Request) -> AttachmentResponse:
    """Record a worker's explicit artifact for ``request_body.name`` against its lease."""
    service = _service(request)
    store: IReadRunnerStore | None = getattr(request.app.state, "runner_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner store not wired — start via `blizzard runner host`",
        )
    lease = store.active_lease(lease_id)
    if lease is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no active lease {lease_id}")
    try:
        service.attach(
            lease,
            presented_token=presented_lease_token(request),
            name=request_body.name,
            content=request_body.content,
        )
    except AttachmentRejected as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return AttachmentResponse(recorded=True, lease_id=lease_id, name=request_body.name)
