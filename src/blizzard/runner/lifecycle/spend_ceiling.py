"""SPEND CEILING — engage the local pause brake once the runner's rolling-window spend reaches its ceiling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.throttle.pause import IWritePauseRepository, PauseService, spend_ceiling_reason
from blizzard.runner.usage.repository import IReadUsageRepository

_log = get_logger("blizzard.runner.loop")


class SpendCeilingConfig(Protocol):
    @property
    def runner_ceiling_usd(self) -> float | None: ...
    @property
    def runner_ceiling_window_hours(self) -> float: ...
    @property
    def runner_name(self) -> str: ...


class SpendCeilingStores(Protocol):
    @property
    def pause(self) -> IWritePauseRepository: ...
    @property
    def usage(self) -> IReadUsageRepository: ...


class SpendCeilingContext(Protocol):
    @property
    def config(self) -> SpendCeilingConfig: ...
    @property
    def stores(self) -> SpendCeilingStores: ...
    @property
    def clock(self) -> IClock: ...
    @property
    def events(self) -> IRunnerEventPublisher | None: ...


@dataclass(frozen=True)
class SpendCeilingCheck:
    ctx: SpendCeilingContext

    def run(self) -> None:
        """Engage the local pause brake once this runner's rolling-window spend reaches
        ``cost.runner_ceiling_usd``; absent, there is no ceiling. Engages exactly once and
        never lifts — only a conscious clear does (tests/unit/runner/test_paused.py)."""
        ctx = self.ctx
        cap = ctx.config.runner_ceiling_usd
        if cap is None:
            return
        if ctx.stores.pause.local_paused():
            return  # already engaged — engage-once; `blizzard runner start` is the only clear
        now = ctx.clock.now()
        window_hours = ctx.config.runner_ceiling_window_hours
        totals = ctx.stores.usage.usage_since(now - timedelta(hours=window_hours))
        if not totals.reaches(cap):
            return
        reason = spend_ceiling_reason(cap=cap, window_hours=window_hours, spend=totals.figure)
        _log.warning(
            f"runner locally paused — {reason}",
            runner_name=ctx.config.runner_name,
            ceiling_usd=cap,
            spend_usd=totals.spend_usd,
            window_hours=window_hours,
            cost_partial=totals.cost_partial,
            estimated_cost_usd=totals.estimated_cost_usd,
        )
        PauseService(ctx.stores.pause, ctx.clock, events=ctx.events).engage(by="runner-ceiling", reason=reason)
