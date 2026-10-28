"""The floor's early samples — a subscription's exhaustion asking the sampling pass not to wait out its cadence.

A fresh mark asks for a sample at once, so the board's usage catches up with what a harness just found; a hold whose
expected reset has passed asks for one so the floor is confirmed open without waiting for the next scheduled sample.
Either fires once: the attempt it records, a miss included, is then the newest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.runner.throttle.gate import subscription_floor
from blizzard.runner.throttle.usage_limits import IReadSubscriptionUsageLimitRepository
from blizzard.runner.usage.external_usage_sample import IEarlySampleTriggers
from blizzard.runner.usage.repository import IReadExternalUsageRepository

__all__ = ["FloorResampleTriggers"]


@dataclass(frozen=True)
class FloorResampleTriggers:
    """A slug is due early when its newest mark is newer than its newest attempt, or when its standing hold's expected
    reset has passed with no attempt since — read only for a slug the pass samples."""

    usage_limits: IReadSubscriptionUsageLimitRepository
    external_usage: IReadExternalUsageRepository

    def due_early(self, slug: str, *, last_attempt_at: datetime | None, now: datetime) -> bool:
        mark = self.usage_limits.latest_usage_limit_mark(slug)
        if mark is not None and (last_attempt_at is None or mark.marked_at > last_attempt_at):
            return True
        floor = subscription_floor(
            slug, sampled=True, usage_limits=self.usage_limits, external_usage=self.external_usage, now=now
        )
        hold = floor.hold
        if hold is None or hold.expected_reset > now:
            return False
        return last_attempt_at is None or last_attempt_at < hold.expected_reset


_floor_resample_triggers_conforms: type[IEarlySampleTriggers] = FloorResampleTriggers
