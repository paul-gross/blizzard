"""Analytics wire bodies (blizzard#254 D7) — the forced re-derive verb's request and
response over ``POST /api/analytics/re-derive``."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReDeriveRequest(BaseModel):
    """Scope the call to one segment (a genuine force, bypassing the candidate check),
    one chunk's candidates, or every candidate (both unset) — never both a segment and a
    chunk. ``limit`` bounds a chunk/all-scoped call; a single segment always derives
    exactly one, so it ignores ``limit``."""

    segment_id: str | None = None
    chunk_id: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


class ReDeriveResponse(BaseModel):
    """How many segments this call derived, and how many still-candidate segments
    remain in scope — the caller drives to convergence by calling again while
    ``remaining`` is nonzero."""

    derived: int
    remaining: int
