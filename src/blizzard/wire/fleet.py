"""Fleet-wide wire views — reads that span every chunk rather than one.

``FleetSpendView`` is a usage/cost total summed at read time over a caller-chosen window,
never a stored column. ``FleetSummaryView`` folds every chunk's derived status to four
buckets (issues #60, #76, #87).
"""

from __future__ import annotations

from pydantic import BaseModel


class FleetSpendView(BaseModel):
    """The fleet's usage/cost total since ``since`` and, when the caller bounded the window, strictly
    before ``until`` (``None`` for the open-ended tail). ``cost_partial`` is ``True`` iff some summed row
    carries neither a billed nor an estimated amount, so ``cost_usd`` is then a lower bound;
    ``estimated_cost_usd`` is ``None`` unless some summed row carried one, and never enters ``cost_usd``."""

    since: str
    until: str | None = None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool
    estimated_cost_usd: float | None = None


class FleetSummaryView(BaseModel):
    """The fleet-pulse counts: ``ready``; ``running`` (``running`` + ``delivering``);
    ``waiting`` (``waiting_on_human`` + ``paused``); ``needs`` (``needs_human``).
    ``not_ready``, ``stopped``, and ``done`` count toward no bucket — a pulse, not a total."""

    ready: int
    running: int
    waiting: int
    needs: int
