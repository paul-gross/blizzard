"""The runner-local fact log — ``GET /api/facts``.

The local panel's "runner store" feed: the newest hub-bound facts off the
outbound buffer, acked or not, newest first — the same ledger DRAIN flushes,
read as history rather than as a pending queue (``bzh:facts-not-status``).
Payloads stay behind — the log reads the event, not the JSON body.

Read-only over its wiring (``bzh:controller-read-only``): the edge holds only the
composition-root-wired :class:`~blizzard.runner.domain.status.RunnerStatusService`.
On the store-free app the service is unwired and the probe answers 503 rather than
pretending.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.wire.runner_status import FactListResponse, FactView

router = APIRouter(prefix="/api", tags=["runner"])

#: The default and ceiling for one page of facts — the panel reads a feed, not the table.
DEFAULT_FACT_LIMIT = 50
MAX_FACT_LIMIT = 200


@router.get("/facts", response_model=FactListResponse)
def list_facts(
    request: Request,
    limit: int = Query(default=DEFAULT_FACT_LIMIT, ge=1, le=MAX_FACT_LIMIT),
) -> FactListResponse:
    """The newest ``limit`` hub-bound facts recorded by this runner, newest first."""
    service: RunnerStatusService | None = getattr(request.app.state, "runner_status", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner status service not wired — start via `blizzard runner host`",
        )
    return FactListResponse(
        items=[
            FactView(
                seq=fact.seq,
                kind=fact.kind,
                chunk_id=fact.chunk_id,
                lease_id=fact.lease_id,
                created_at=iso_utc(fact.created_at),
                acked_at=iso_utc(fact.acked_at) if fact.acked_at is not None else None,
            )
            for fact in service.recent_facts(limit)
        ]
    )
