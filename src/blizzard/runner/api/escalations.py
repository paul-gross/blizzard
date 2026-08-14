"""The runner-local escalations list — ``GET /api/escalations`` (issue #51).

Every chunk escalated to needs-human and not yet superseded, each with
its ready-to-paste resume command. Derived at read time, with that command **recomputed**
rather than read back off the outbound buffer, which holds only the unacked tail."""

from __future__ import annotations

from fastapi import APIRouter, Request

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.wire.runner_status import EscalationListResponse
from blizzard.wire.runner_status import EscalationView as EscalationViewWire

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/escalations", response_model=EscalationListResponse)
def list_escalations(request: Request) -> EscalationListResponse:
    """Every escalation still open — no later lease mint, and the hub has not ended the chunk."""
    return _escalation_list(RunnerWiring.of(request).status())


def _escalation_list(service: RunnerStatusService) -> EscalationListResponse:
    return EscalationListResponse(
        items=[
            EscalationViewWire(
                chunk_id=e.chunk_id,
                lease_id=e.lease_id,
                node_id=e.node_id,
                epoch=e.epoch,
                closed_at=iso_utc(e.closed_at),
                resume_command=e.resume_command,
                session_name=e.session_name,
                model=e.model,
                effort=e.effort,
            )
            for e in service.escalations()
        ]
    )
