"""The throttle gate — may this runner start? asked before every claim and every process start.

The breakers answer first and a tripped one ends the question; the throttles govern only a runner no breaker holds.
:class:`GateVerdict` decides by value over the throttles' answers; :class:`ThrottleGate` gathers their readings fresh on
every ask, so a limit a neighbour marked a moment earlier is seen at once."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.runner.subscriptions.subscription_sampler import SAMPLED_PROVIDERS
from blizzard.runner.throttle.floor import FloorAnswer, floor_answer
from blizzard.runner.throttle.pause import RunnerBrakes
from blizzard.runner.throttle.usage_limits import IReadRunnerUsageLimitRepository, IReadSubscriptionUsageLimitRepository
from blizzard.runner.usage.repository import IReadExternalUsageRepository

__all__ = [
    "ClaimsAnswer",
    "GateConfig",
    "GateContext",
    "GateHostStores",
    "GateReadConfig",
    "GateReadContext",
    "GateStores",
    "GateSubscription",
    "GateVerdict",
    "StartKind",
    "ThrottleGate",
    "subscription_floor",
]


class StartKind(StrEnum):
    """What a start is: a fresh lease for a node-step, a continuation of a step already begun, or a zero-budget mint
    that exists only to escalate a chunk and so answers to the breakers alone."""

    STEP = "step"
    CONTINUATION = "continuation"
    ESCALATION = "escalation"


@domain_model
@dataclass(frozen=True)
class GateVerdict:
    """The throttles' answers together — the subscription floor's, the only throttle there is."""

    floor: FloorAnswer

    @property
    def admits_claim(self) -> bool:
        """FILL may peek and claim."""
        return self.floor.admits

    @property
    def admits_step(self) -> bool:
        """A fresh lease may be minted for a node-step."""
        return self.floor.admits

    @property
    def admits_continuation(self) -> bool:
        """A step already begun may continue — the floor alone decides, since a start against an exhausted
        subscription meets the wall whatever kind of start it is."""
        return self.floor.admits

    @property
    def declining(self) -> tuple[FloorAnswer, ...]:
        """Every answer saying no, the floor's first."""
        return self._declining()

    def _declining(self) -> tuple[FloorAnswer, ...]:
        return () if self.floor.admits else (self.floor,)

    def admits(self, start: StartKind) -> bool:
        """Whether ``start`` may proceed; an escalation spends nothing, so no throttle holds it."""
        if start is StartKind.STEP:
            return self.admits_step
        if start is StartKind.CONTINUATION:
            return self.admits_continuation
        return True


@domain_model
@dataclass(frozen=True)
class ClaimsAnswer:
    """FILL's one answer: the brakes it was asked with, and the verdict when no brake already refused claims."""

    brakes: RunnerBrakes
    verdict: GateVerdict | None

    @property
    def braked(self) -> bool:
        """A brake refuses new claims; the throttles were not asked."""
        return self.brakes.blocks_claims

    @property
    def declining(self) -> GateVerdict | None:
        """The verdict, when it declines a claim."""
        return self._declining()

    def _declining(self) -> GateVerdict | None:
        if self.verdict is None or self.verdict.admits_claim:
            return None
        return self.verdict

    @property
    def held_back(self) -> bool:
        """A brake or the gate keeps this runner from claiming."""
        return self._held_back()

    def _held_back(self) -> bool:
        return self.braked or self.declining is not None


class GateSubscription(Protocol):
    """One declared subscription, as the gate reads it."""

    @property
    def slug(self) -> str: ...
    @property
    def provider(self) -> str: ...


class GateConfig(Protocol):
    @property
    def subscription(self) -> str | None: ...


class GateStores(Protocol):
    @property
    def usage_limits(self) -> IReadRunnerUsageLimitRepository: ...


class GateHostStores(Protocol):
    @property
    def external_usage(self) -> IReadExternalUsageRepository: ...
    @property
    def usage_limits(self) -> IReadSubscriptionUsageLimitRepository: ...


class GateContext(Protocol):
    """What the gate reads: the subscription the runner spends, its marks, clears and samples, and the clock."""

    @property
    def stores(self) -> GateStores: ...
    @property
    def host_stores(self) -> GateHostStores: ...
    @property
    def config(self) -> GateConfig: ...
    @property
    def clock(self) -> IClock: ...
    @property
    def subscriptions(self) -> Sequence[GateSubscription]: ...


@domain_model
@dataclass(frozen=True)
class GateReadConfig:
    subscription: str | None


@dataclass(frozen=True)
class GateReadContext:
    """The status reader's narrow, runner-scoped assembly of the gate's existing seams."""

    stores: GateStores
    host_stores: GateHostStores
    config: GateConfig
    clock: IClock
    subscriptions: Sequence[GateSubscription]


@dataclass(frozen=True)
class ThrottleGate:
    """Gathers the throttles' readings and asks for the verdict — nothing cached across asks."""

    ctx: GateContext

    def verdict(self) -> GateVerdict:
        """The throttles' verdict as the readings stand now."""
        return GateVerdict(floor=self.floor())

    def claims(self, brakes: RunnerBrakes) -> ClaimsAnswer:
        """FILL's ask: ``brakes``' refusal, else the verdict. A braked runner's throttles are never read."""
        if brakes.blocks_claims:
            return ClaimsAnswer(brakes=brakes, verdict=None)
        return ClaimsAnswer(brakes=brakes, verdict=self.verdict())

    def floor(self) -> FloorAnswer:
        """The runner's floor: its subscription's, or its own when it names none."""
        ctx = self.ctx
        now = ctx.clock.now()
        slug = ctx.config.subscription
        if slug is None:
            runner = ctx.stores.usage_limits
            return floor_answer(
                slug=None,
                sampled=False,
                mark=runner.latest_usage_limit_mark(),
                sample=None,
                clear=runner.latest_floor_clear(),
                now=now,
            )
        sampled = any(s.slug == slug and s.provider in SAMPLED_PROVIDERS for s in ctx.subscriptions)
        return subscription_floor(
            slug,
            sampled=sampled,
            usage_limits=ctx.host_stores.usage_limits,
            external_usage=ctx.host_stores.external_usage,
            now=now,
        )


def subscription_floor(
    slug: str,
    *,
    sampled: bool,
    usage_limits: IReadSubscriptionUsageLimitRepository,
    external_usage: IReadExternalUsageRepository,
    now: datetime,
) -> FloorAnswer:
    """``slug``'s floor at ``now``, read fresh: its newest mark, newest sample and newest clear — and, when a sample
    came after the clear, the sample standing at the clear, whose exhausted windows the clear set aside."""
    sample = external_usage.latest_external_usage_samples_by_slug((slug,)).get(slug) if sampled else None
    clear = usage_limits.latest_floor_clear(slug)
    clear_sample = None
    if clear is not None and sample is not None and sample.sampled_at > clear.cleared_at:
        clear_sample = external_usage.external_usage_sample_at(slug, clear.cleared_at)
    return floor_answer(
        slug=slug,
        sampled=sampled,
        mark=usage_limits.latest_usage_limit_mark(slug),
        sample=sample,
        clear=clear,
        clear_sample=clear_sample,
        now=now,
    )
