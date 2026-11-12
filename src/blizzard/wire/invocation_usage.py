"""Runner-local, chunk-filtered invocation provenance."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel

from blizzard.foundation.unrecognized import Unrecognized
from blizzard.foundation.usage_kind import UsageKind
from blizzard.wire.vocabulary import OPEN


class InvocationUsageView(BaseModel):
    lease_id: str
    chunk_id: str
    node_id: str
    epoch: int
    generation: int
    kind: Annotated[UsageKind | Unrecognized, OPEN]
    harness_id: str | None
    model: str | None
    recorded_at: str


class InvocationUsagePage(BaseModel):
    chunk_id: str
    items: list[InvocationUsageView]
    limit: int
    offset: int
