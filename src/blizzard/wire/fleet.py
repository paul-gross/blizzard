"""Fleet-wide wire views — reads that span every chunk rather than one.

``FleetSpendView`` is the ``GET /api/fleet/spend`` read's shape (issue #60): a
fleet-wide usage/cost total, summed at read time over every usage fact recorded at or
after a caller-chosen instant (:func:`~blizzard.hub.domain.work.derive_fleet_usage`) —
never a stored column, same as a chunk's own derived total
(:class:`~blizzard.wire.chunk.ChunkUsageTotalView`).
"""

from __future__ import annotations

from pydantic import BaseModel


class FleetSpendView(BaseModel):
    """The fleet's usage/cost total since ``since``. ``cost_partial`` carries the
    lower-bound + PARTIAL contract on ``cost_usd`` — see
    :class:`~blizzard.hub.domain.work.UsageTotal` for the one canonical statement of
    it, which this view's fields mirror verbatim."""

    since: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool
