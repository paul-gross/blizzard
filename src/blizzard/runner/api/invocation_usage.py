"""A bounded runner-local read of a chunk's recorded invocation provenance."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.invocation_usage import InvocationUsagePage, InvocationUsageView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/chunks/{chunk_id}/usage", response_model=InvocationUsagePage)
def chunk_invocation_usage(
    chunk_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> InvocationUsagePage:
    """The runner's own invocation records, not a hub-wide accounting view."""
    wiring = RunnerWiring.of(request)
    wiring.require_bound()
    rows = wiring.read_stores().usage.invocations_for_chunk(chunk_id, limit=limit, offset=offset)
    return InvocationUsagePage(
        chunk_id=chunk_id,
        items=[
            InvocationUsageView(
                lease_id=row.lease_id,
                chunk_id=row.chunk_id,
                node_id=row.node_id,
                epoch=row.epoch,
                generation=row.generation,
                kind=row.kind,
                harness_id=row.harness_id,
                model=row.model,
                recorded_at=iso_utc(row.recorded_at),
            )
            for row in rows
        ],
        limit=limit,
        offset=offset,
    )
