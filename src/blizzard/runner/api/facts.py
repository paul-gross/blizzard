"""The runner-local fact log — ``GET /api/facts``.

The newest hub-bound facts off the outbound buffer, acked or not, newest first — read as
history, not as a pending queue (``bzh:facts-not-status``). Payloads stay behind, and the
read is read-only over its wiring (``bzh:controller-read-only``)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
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
    service = RunnerWiring.of(request).status()
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
