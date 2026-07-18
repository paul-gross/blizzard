"""The runner-local escalations list — ``GET /api/escalations`` (issue #51).

``blizzard runner status``'s parked-escalations section: every chunk this runner
has escalated to needs-human and not yet seen superseded by a later lease mint,
each carrying its literal, ready-to-paste resume command. Derived at read time from
the closed-``escalated`` lease facts ``_escalate`` (``runner/loop/steps.py``) records
plus the chunk's still-held binding — the resume command is **recomputed** from the
harness adapter, not read back off the outbound buffer (which only holds the unacked
tail and would go blank the moment the fact flushes to the hub).

Read-only over its wiring (``bzh:controller-read-only``): the edge holds only the
composition-root-wired :class:`~blizzard.runner.domain.status.RunnerStatusService`.
On the store-free app the service is unwired and the probe answers 503 rather than
pretending.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.wire.runner_status import EscalationListResponse
from blizzard.wire.runner_status import EscalationView as EscalationViewWire

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/escalations", response_model=EscalationListResponse)
def list_escalations(request: Request) -> EscalationListResponse:
    """Every escalation still open — no later lease mint has superseded it."""
    service: RunnerStatusService | None = getattr(request.app.state, "runner_status", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner status service not wired — start via `blizzard runner host`",
        )
    return EscalationListResponse(
        items=[
            EscalationViewWire(
                chunk_id=e.chunk_id,
                lease_id=e.lease_id,
                node_id=e.node_id,
                epoch=e.epoch,
                closed_at=iso_utc(e.closed_at),
                resume_command=e.resume_command,
            )
            for e in service.escalations()
        ]
    )
