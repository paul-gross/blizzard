"""``blizzard runner artifact create`` — ``POST /api/leases/{lease_id}/attachments``
plus its read-back counterpart ``GET``.

Token presentation is owned by ``src/blizzard/runner/api/lease_token.py``. ``404`` unknown/closed lease, ``403`` bad
token, ``409`` an open takeover's closed reference lease or an outcome already buffered, ``422`` empty content or
content the hub refuses for the format its graph owes the name (nothing is recorded)."""

from __future__ import annotations

import httpx2
from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.logging import get_logger
from blizzard.runner.api.hub_proxy import RELAYED_REFUSALS, HubProxy
from blizzard.runner.api.lease_scope import authorized_lease, authorized_worker_lease
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.operator.attachments import AttachmentEmpty, AttachmentOnClosedLease, AttachmentTooLate
from blizzard.wire.attachments import (
    ArtifactCheckRequest,
    ArtifactCheckResponse,
    AttachmentRequest,
    AttachmentResponse,
    StagedAttachment,
)

router = APIRouter(prefix="/api", tags=["runner"])

#: The configured bound on the advisory stage-time format check — below the CLI's ``WRITE_TIMEOUT``, so a
#: slow hub is meant to cost the worker a skipped check rather than a failed submission. It bounds the check,
#: not the whole request, and promises nothing about the CLI never timing out. Pinned at
#: tests/unit/runner/cli/test_artifact.py::test_the_stage_check_budget_fits_inside_the_cli_write_timeout.
STAGE_CHECK_TIMEOUT = 3.0

_log = get_logger(__name__)


def _hub_refusal(request: Request, chunk_id: str, body: AttachmentRequest) -> str | None:
    """The hub's refusal of ``body``'s content for staging, or ``None`` when it passes — and when there is no
    usable advisory verdict (transport failure or timeout, an old hub's 404, an upstream auth or identity
    refusal, a malformed answer): staging is a durable runner-local save, not hub acceptance, and delivery
    stays authoritative."""
    try:
        upstream = HubProxy.of(request, "attachments").post(
            f"/api/fleet/chunks/{chunk_id}/artifact-check",
            expect=status.HTTP_200_OK,
            timeout=STAGE_CHECK_TIMEOUT,
            severity="warning",
            json_body=ArtifactCheckRequest(name=body.name, content=body.content).model_dump(),
            chunk_id=chunk_id,
        )
        return ArtifactCheckResponse.model_validate(upstream.json()).refusal
    except (HTTPException, httpx2.HTTPError, ValueError) as exc:
        _log.warning("stage-time artifact check unavailable; staging unchecked", chunk_id=chunk_id, error=str(exc))
        return None


@router.post(
    "/leases/{lease_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_200_OK,
    responses=RELAYED_REFUSALS,
)
def record_attachment(lease_id: str, request_body: AttachmentRequest, request: Request) -> AttachmentResponse:
    """Record a worker's explicit artifact for ``request_body.name`` against its lease."""
    service = RunnerWiring.of(request).attachments()
    worker = authorized_worker_lease(lease_id, request)
    try:
        # Lifecycle refusals (empty, closed lease, outcome already buffered) take precedence over the
        # hub's judgement of the content's format.
        service.require_attachable(worker, name=request_body.name, content=request_body.content)
        refusal = _hub_refusal(request, worker.lease.chunk_id, request_body)
        if refusal is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=refusal)
        service.attach(worker, name=request_body.name, content=request_body.content)
    except AttachmentEmpty as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except (AttachmentOnClosedLease, AttachmentTooLate) as exc:
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
