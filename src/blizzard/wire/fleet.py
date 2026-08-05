"""Fleet-wide wire views — reads that span every chunk rather than one.

``FleetSpendView`` is a usage/cost total summed at read time over a caller-chosen window,
never a stored column. ``FleetSummaryView`` folds every chunk's derived status to four
buckets (issues #60, #76, #87).
"""

from __future__ import annotations

from pydantic import BaseModel


class FleetSpendView(BaseModel):
    """The fleet's usage/cost total since ``since`` and, when the caller bounded the
    window, strictly before ``until`` (``None`` for the open-ended tail). ``cost_partial``
    marks ``cost_usd`` as a lower bound — see :class:`~blizzard.hub.domain.work.UsageTotal`."""

    since: str
    until: str | None = None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool


class FleetSummaryView(BaseModel):
    """The fleet-pulse counts: ``ready``; ``running`` (``running`` + ``delivering``);
    ``waiting`` (``waiting_on_human`` + ``paused``); ``needs`` (``needs_human``).
    ``not_ready``, ``stopped``, and ``done`` count toward no bucket — a pulse, not a total."""

    ready: int
    running: int
    waiting: int
    needs: int
