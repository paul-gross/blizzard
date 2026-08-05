"""The harness-usage domain value (epic #57, phase 1 of #58).

Cost always comes from the harness's own reported figure — blizzard never maintains a
pricing table. Token counts are always present, but ``cost_usd`` can be legitimately
absent: ``None`` means no result envelope existed, never a fabricated ``0.0``, and a
caller summing cost must read it as "unknown" rather than zero."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The point in a node's lifecycle an invocation is attributed to. Supplied by the
#: caller, which knows the operation it just ran, never inferred by the adapter.
UsageKind = Literal["spawn", "resume", "judge", "nudge"]

__all__ = ["UsageKind", "UsageSample"]


@dataclass(frozen=True)
class UsageSample:
    """Token usage + cost for one harness invocation.

    The four token counts are kept apart because they price differently. ``model`` is
    the harness-reported id for this invocation, never a configured default."""

    kind: UsageKind
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float | None
