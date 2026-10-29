"""Runner-local, chunk-filtered invocation provenance."""

from __future__ import annotations

from pydantic import BaseModel


class InvocationUsageView(BaseModel):
    lease_id: str
    chunk_id: str
    node_id: str
    epoch: int
    generation: int
    kind: str
    harness_id: str
    model: str | None
    recorded_at: str


class InvocationUsagePage(BaseModel):
    chunk_id: str
    items: list[InvocationUsageView]
    limit: int
    offset: int
