"""The harness-usage domain value (epic #57, issue #58).

Cost always comes from the harness's own reported figure — blizzard never maintains a
pricing table. Token counts are always present, but ``cost_usd`` can be legitimately
absent: ``None`` means no result envelope existed, never a fabricated ``0.0``, and a
caller summing cost must read it as "unknown" rather than zero."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The point in a node's lifecycle an invocation is attributed to. Supplied by the
#: caller, which knows the operation it just ran, never inferred by the adapter.
UsageKind = Literal["spawn", "resume", "judge"]

__all__ = ["SessionCostBasis", "UsageKind", "UsageSample", "invocation_cost"]


@dataclass(frozen=True)
class UsageSample:
    """Token usage + cost for one harness invocation.

    The four token counts are kept apart because they price differently, and are always
    this invocation's own. ``model`` is the harness-reported id, never a configured one."""

    kind: UsageKind
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float | None
    #: What ``cost_usd`` covers; ``None`` says the figure is this invocation's alone.
    cost_scope_tokens: int | None = None

    @property
    def token_total(self) -> int:
        """This invocation's own four counts summed."""
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_create_tokens


@dataclass(frozen=True)
class SessionCostBasis:
    """What one session's banked facts establish, for reading its next reported figure."""

    #: Every banked invocation's tokens summed.
    token_total: int
    #: Every banked invocation's own cost summed — what a session-scoped figure grew from.
    banked_cost_usd: float


def invocation_cost(sample: UsageSample, prior: SessionCostBasis | None) -> float | None:
    """What ``sample`` alone cost, reading its reported figure against ``prior``.

    ``None`` is cost unknown, which every total already carries as PARTIAL. The two
    readings, and the one shape they cannot be told apart in, are owned by
    ``docs/deployment/spend.md``."""
    if sample.cost_usd is None:
        return None
    # Nothing banked yet, or nothing reported to read against: the readings coincide.
    if sample.cost_scope_tokens is None or prior is None or prior.token_total <= 0:
        return sample.cost_usd
    own = sample.token_total
    if abs(sample.cost_scope_tokens - (prior.token_total + own)) > abs(sample.cost_scope_tokens - own):
        return sample.cost_usd
    delta = sample.cost_usd - prior.banked_cost_usd
    # Backwards is a reading this cannot make sense of, so it says so rather than bank 0.
    return max(delta, 0.0) if delta > -1e-9 else None
