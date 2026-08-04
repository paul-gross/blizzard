"""Fleet-wide wire views — reads that span every chunk rather than one.

``FleetSpendView`` is the ``GET /api/spend`` read's shape (issues #60, #87): a
fleet-wide usage/cost total, summed at read time over every usage fact recorded at or
after a caller-chosen instant (:func:`~blizzard.hub.domain.work.derive_fleet_usage`) —
never a stored column.

``FleetSummaryView`` is the ``GET /api/fleet/summary`` fleet-pulse read (issue #76):
every chunk's derived status folded to four buckets
(:func:`~blizzard.hub.domain.work.derive_fleet_summary`).
"""

from __future__ import annotations

from pydantic import BaseModel


class FleetSpendView(BaseModel):
    """The fleet's usage/cost total since ``since`` — and, when the caller bounded the
    window, strictly before ``until`` (issue #183; ``None`` for the original open-ended
    tail). ``cost_partial`` carries the lower-bound + PARTIAL contract on ``cost_usd`` —
    see :class:`~blizzard.hub.domain.work.UsageTotal` for the one canonical statement of
    it, which this view's fields mirror verbatim."""

    since: str
    until: str | None = None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool


class FleetSummaryView(BaseModel):
    """The fleet-pulse counts (issue #76) — every chunk's derived status folded to four
    buckets:

    * ``ready`` — chunks derived ``ready``;
    * ``running`` — ``running`` + ``delivering`` (live work, either shape);
    * ``waiting`` — ``waiting_on_human`` + ``paused`` (human-parked);
    * ``needs`` — ``needs_human``.

    The remaining derived statuses (``not_ready``, ``stopped``, ``done``) count toward no
    bucket — a live-work pulse, not a total. The fold's canonical statement:
    :func:`~blizzard.hub.domain.work.derive_fleet_summary`."""

    ready: int
    running: int
    waiting: int
    needs: int
