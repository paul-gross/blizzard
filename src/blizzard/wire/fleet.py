"""Fleet-wide wire views — the fleet spend total and the fleet status summary."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.base import WireModel


class FleetSpendView(WireModel):
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
