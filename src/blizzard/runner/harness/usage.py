"""The harness-usage domain value (epic #57, phase 1 of #58).

Cost always comes from the harness's own ``total_cost_usd`` — blizzard never
maintains a pricing table (``bzh:facts-not-status`` precedent: a derived figure is
read off the source of truth at read time, never recomputed from a private
schedule). :class:`UsageSample` is the one shape a coding-harness invocation's
consumption is translated into, produced behind :class:`~blizzard.runner.harness.
adapter.IHarnessAdapter` (``parse_usage`` / ``sum_transcript_usage``) so a future
Codex/OpenCode adapter conforms without the core ever learning a second harness's
wire format.

Token counts are always present (a killed or reaped worker still consumed tokens);
``cost_usd`` is the one field that can be legitimately absent — ``None`` means no
result envelope existed for this invocation (the envelope-less transcript-summation
fallback, or a process that never produced one), never a fabricated ``0.0``. A
caller that sums cost across samples must treat ``None`` as "unknown", not as
zero-cost (the runner-ceiling/chunk-cap phases carry this forward as the
lower-bound + PARTIAL treatment).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The three points in a node's lifecycle a harness invocation is attributed to —
#: the initial spawn, a fire-and-forget resume (answer delivery / CI feedback), or
#: the two-phase judgement resume. Supplied by the caller (the runner core knows
#: which operation it just ran), never inferred by the adapter.
UsageKind = Literal["spawn", "resume", "judge"]

__all__ = ["UsageKind", "UsageSample"]


@dataclass(frozen=True)
class UsageSample:
    """Token usage + cost for one harness invocation.

    Token counts follow the harness result-envelope's own ``usage`` object's
    class split: ``input_tokens``/``output_tokens`` are billed fresh,
    ``cache_read_tokens``/``cache_create_tokens`` are the prompt-cache halves
    (a cache read is far cheaper than a fresh input token; a cache create costs
    more than one — collapsing them into a single "input" figure would hide that).
    ``model`` is the harness-reported model id for this invocation, not a
    runner-side configured default, so a per-model cost breakdown is possible
    later without re-deriving it from a spawn-time constant.
    """

    kind: UsageKind
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float | None
