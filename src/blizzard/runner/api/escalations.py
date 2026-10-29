"""The runner-local escalations list — ``GET /api/escalations``.

Every chunk escalated to needs-human and not yet superseded, each with
its ready-to-paste resume command — or, under ``state=closed``, the newest page of those the
hub ended, each with its closure. Derived at read time, with that command **recomputed**
rather than read back off the outbound buffer, which holds only the unacked tail."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.environments.repository import IReadEnvironmentRepository
from blizzard.runner.status.view import EscalationView, RunnerStatusService
from blizzard.wire.runner_status import EscalationClosureView, EscalationListResponse
from blizzard.wire.runner_status import EscalationView as EscalationViewWire

router = APIRouter(prefix="/api", tags=["runner"])

#: The default and ceiling for one page of closed escalations — a history, not the live set.
DEFAULT_CLOSED_ESCALATION_LIMIT = 50
MAX_CLOSED_ESCALATION_LIMIT = 200


@router.get("/escalations", response_model=EscalationListResponse)
def list_escalations(
    request: Request,
    state: Literal["open", "closed"] = Query(default="open"),
    limit: int = Query(default=DEFAULT_CLOSED_ESCALATION_LIMIT, ge=1, le=MAX_CLOSED_ESCALATION_LIMIT),
) -> EscalationListResponse:
    """Every escalation still open — no later lease mint, and the hub has not ended the chunk — or, under
    ``state=closed``, the newest ``limit`` the hub ended, newest closure first, one per chunk.

    ``limit`` pages only the closed history: the open set is bounded by the chunks parked on this
    runner now, not by its history, and the dashboard renders it whole."""
    wiring = RunnerWiring.of(request)
    service = wiring.status()
    environments = wiring.read_stores().environments
    if state == "closed":
        return _escalation_items(service.closed_escalations(limit), environments)
    return _escalation_list(service, environments)


def _escalation_list(service: RunnerStatusService, environments: IReadEnvironmentRepository) -> EscalationListResponse:
    return _escalation_items(service.escalations(), environments)


def _escalation_items(
    escalations: list[EscalationView], environments: IReadEnvironmentRepository
) -> EscalationListResponse:
    projects = environments.chunk_project_ids(sorted({e.chunk_id for e in escalations}))
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
                harness_id=e.harness_id,
                harness_version=e.harness_version,
                wrapped_takeover_command=e.wrapped_takeover_command,
                cause=e.cause,
                project_id=projects.get(e.chunk_id),
                closure=EscalationClosureView(reason=e.closure.reason, closed_at=iso_utc(e.closure.closed_at))
                if e.closure is not None
                else None,
            )
            for e in escalations
        ]
    )
