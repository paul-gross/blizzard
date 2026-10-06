"""Runner→hub fact intake bodies.

Two coexisting intakes for the same runner-minted facts: a batched store-and-forward push,
where each fact carries a **per-runner monotonic seq** applied idempotently against a
per-runner **high-water mark**, and a direct per-fact body for landing a single fact.
Completions ride neither, since they carry the next-node envelope in their reply."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ExternalSubscriptionUsageWindowFact(BaseModel):
    """One complete subscription-usage window accepted from a runner fact.

    The numeric fields are strict: a ``bool`` is refused where a number belongs.
    ``resets_at`` stays lax and accepts an ISO-8601 string."""

    window: str = Field(strict=True)
    utilization_pct: float = Field(ge=0, le=100, allow_inf_nan=False, strict=True)
    resets_at: datetime
    window_seconds: int = Field(gt=0, strict=True)


class RunnerFact(BaseModel):
    """One buffered runner fact: its per-runner seq, its kind, and its payload.

    ``payload`` is the kind-specific body, kept open so a new fact kind needs no wire change;
    every chunk-scoped kind carries ``route_token``, stamped at enqueue."""

    seq: int
    kind: str
    payload: dict[str, Any] = {}


class RunnerFactBatch(BaseModel):
    """A runner's push of one-or-more buffered facts, ordered by seq — attributed to the runner
    its bearer token names; the body carries no runner id."""

    facts: list[RunnerFact]


class RunnerFactAck(BaseModel):
    """The hub's per-batch acknowledgement against its high-water mark.

    ``high_water`` is the new mark after this batch; ``applied``/``already_applied`` partition
    the pushed seqs, and ``rejected`` names seqs refused for a non-idempotency reason. ``route_ended`` is the subset of
    ``rejected`` refused because the fact's chunk has no live route."""

    runner_id: str
    high_water: int
    applied: list[int] = []
    already_applied: list[int] = []
    rejected: list[int] = []
    route_ended: list[int] = []
