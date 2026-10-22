"""The usage-limit mark and the floor clear, and their repository seams.

A mark records that one invocation reported a usage limit; a clear records that someone opened a floor by hand. Both
are append-only facts, kept against a subscription on the host or against the runner itself when it spends none — the
floor is derived from the newest of each, never stored. A mark is unique on its invocation, so a replayed mark records
nothing new."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.leases.overload import InvocationKind

__all__ = [
    "FloorClear",
    "IReadRunnerUsageLimitRepository",
    "IReadSubscriptionUsageLimitRepository",
    "IWriteRunnerUsageLimitRepository",
    "IWriteSubscriptionUsageLimitRepository",
    "UsageLimitMark",
]


@domain_model
@dataclass(frozen=True)
class UsageLimitMark:
    """One invocation's usage limit: when it was marked, the reset its harness named (``None`` when it named none), the
    sampled window the limit fell in and that window's reset (both ``None`` when no sample placed it), and the
    invocation that reported it."""

    marked_at: datetime
    resets_at: datetime | None
    window: str | None
    window_resets_at: datetime | None
    harness_id: str
    detail: str
    lease_id: str
    epoch: int
    generation: int
    invocation_kind: InvocationKind


@domain_model
@dataclass(frozen=True)
class FloorClear:
    """A floor opened by hand: when, and who opened it."""

    cleared_at: datetime
    by: str


class IReadSubscriptionUsageLimitRepository(Protocol):
    """Read a subscription's newest mark and clear — host-scoped, shared by every runner spending it."""

    def latest_usage_limit_mark(self, slug: str) -> UsageLimitMark | None:
        """The subscription's newest mark by ``marked_at``, a same-instant tie going to the later row; ``None`` if it
        holds none."""
        ...

    def latest_floor_clear(self, slug: str) -> FloorClear | None:
        """The subscription's newest clear by ``cleared_at``, a same-instant tie going to the later row; ``None`` if it
        holds none."""
        ...


class IWriteSubscriptionUsageLimitRepository(IReadSubscriptionUsageLimitRepository, Protocol):
    """Mark, clear and prune a subscription's floor facts — held by the usage-limit path, the clear route and
    retention."""

    def record_usage_limit_mark(self, slug: str, mark: UsageLimitMark) -> bool:
        """Record ``mark`` against ``slug``, unless its invocation (lease, epoch, generation, kind) is already marked;
        whether a row was written."""
        ...

    def record_floor_clear(self, slug: str, clear: FloorClear) -> None:
        """Record ``clear`` against ``slug``."""
        ...

    def prune_usage_limits(self, *, now: datetime) -> int:
        """Delete marks and clears older than the store's own retention window, keeping each subscription's newest of
        each regardless of age, so the latest reads answer identically before and after. Returns the rows pruned."""
        ...


class IReadRunnerUsageLimitRepository(Protocol):
    """Read the bound runner's own newest mark and clear — the floor of a runner spending no subscription."""

    def latest_usage_limit_mark(self) -> UsageLimitMark | None:
        """The runner's newest mark by ``marked_at``, a same-instant tie going to the later row; ``None`` if it holds
        none."""
        ...

    def latest_floor_clear(self) -> FloorClear | None:
        """The runner's newest clear by ``cleared_at``, a same-instant tie going to the later row; ``None`` if it holds
        none."""
        ...


class IWriteRunnerUsageLimitRepository(IReadRunnerUsageLimitRepository, Protocol):
    """Mark, clear and prune the bound runner's own floor facts."""

    def record_usage_limit_mark(self, mark: UsageLimitMark) -> bool:
        """Record ``mark`` against the runner, unless its invocation is already marked; whether a row was written."""
        ...

    def record_floor_clear(self, clear: FloorClear) -> None:
        """Record ``clear`` against the runner."""
        ...

    def prune_usage_limits(self, *, now: datetime) -> int:
        """Delete the runner's marks and clears older than the store's own retention window, keeping its newest of
        each regardless of age. Returns the rows pruned."""
        ...
