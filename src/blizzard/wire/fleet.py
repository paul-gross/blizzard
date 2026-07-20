"""Fleet-wide wire views — reads that span every chunk rather than one.

``FleetSpendView`` is the ``GET /api/spend`` read's shape (issue #60, relocated from
``/api/fleet/spend`` by issue #87 to free that prefix for the runner-authenticated
fleet router): a
fleet-wide usage/cost total, summed at read time over every usage fact recorded at or
after a caller-chosen instant (:func:`~blizzard.hub.domain.work.derive_fleet_usage`) —
never a stored column, same as a chunk's own derived total
(:class:`~blizzard.wire.chunk.ChunkUsageTotalView`).

``FleetSummaryView`` is the runner machine panel's fleet-pulse read (issue #76): every
chunk's derived status folded to the four buckets the counts strip shows
(:func:`~blizzard.hub.domain.work.derive_fleet_summary`). The runner reaches it through
its own local pass-through (:mod:`blizzard.runner.api.fleet_summary`), which forwards to
the fleet router's ``GET /api/fleet/summary`` — the same layered read as the PM-items
proxy, four integers on the wire rather than the chunk list.
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


class FleetSummaryView(BaseModel):
    """The runner machine panel's fleet-pulse counts (issue #76) — every chunk's derived
    status folded to the four buckets the counts strip shows:

    * ``ready`` — chunks derived ``ready``;
    * ``running`` — ``running`` + ``delivering`` (live work, either shape);
    * ``waiting`` — ``waiting_on_human`` + ``paused`` (human-parked);
    * ``needs`` — ``needs_human``.

    The remaining derived statuses (``not_ready``, ``stopped``, ``done``) count toward no
    bucket — the strip is a live-work pulse, not a total. See
    :func:`~blizzard.hub.domain.work.derive_fleet_summary` for the single canonical
    statement of the fold, which these fields mirror."""

    ready: int
    running: int
    waiting: int
    needs: int
