"""The poisoned-tenant fixture (``bzh:pass-isolates-tenants``): runs a hub-wide pass twice on the virtual clock —
once with one tenant poisoned, once without that tenant — and fails when any other tenant's recorded calls differ.
A pass's builder wires it over fakes that log each call into a tenant's half through the :class:`Recorder`, and
returns its ``sweep``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from blizzard.foundation.clock import FixedClock
from blizzard.hub.tenancy import TenantId

#: Sorts before, and leads the corpus ahead of, the healthy tenants, so a leak reaches them.
POISONED = TenantId("ten_a")
HEALTHY: tuple[TenantId, ...] = (TenantId("ten_b"), TenantId("ten_c"))

START = datetime(2026, 7, 13, tzinfo=UTC)
INTERVAL = timedelta(minutes=1)


class Poison(Enum):
    RAISES = "raises"  # the tenant's unit raises on every call
    UNCONVERGED = "unconverged"  # the tenant's unit reports it did not converge, on every call
    BURST = "burst"  # the tenant holds more due work than a pass can take


@dataclass(frozen=True)
class Scene:
    """Who is in the run: ``poisoned`` is ``None`` for the run without the poisoned tenant."""

    poisoned: TenantId | None
    poison: Poison | None

    @property
    def tenants(self) -> tuple[TenantId, ...]:
        return (*HEALTHY, *([self.poisoned] if self.poisoned else []))


class Recorder:
    """What a run's passes did, per tenant: each call, beside the pass it was made in."""

    def __init__(self) -> None:
        self.pass_no = 0
        self.calls: dict[TenantId, list[tuple[object, ...]]] = {}

    def log(self, tenant: TenantId, *call: object) -> None:
        self.calls.setdefault(tenant, []).append((self.pass_no, *call))


Build = Callable[[Scene, Recorder, FixedClock], Callable[[], None]]


def _run(build: Build, scene: Scene, passes: int) -> Recorder:
    recorder, clock = Recorder(), FixedClock(START)
    sweep = build(scene, recorder, clock)
    for number in range(passes):
        recorder.pass_no = number
        sweep()
        clock.advance(INTERVAL)
    return recorder


def assert_pass_isolates(build: Build, poison: Poison, *, passes: int = 14) -> None:
    """Run ``build``'s pass for ``passes`` one-minute steps with ``POISONED`` poisoned by ``poison`` and without it, and
    assert every healthy tenant's recorded calls are identical across the two runs, and that the poisoned run did
    something for each of them."""
    poisoned = _run(build, Scene(POISONED, poison), passes)
    alone = _run(build, Scene(None, None), passes)
    for tenant in HEALTHY:
        assert alone.calls.get(tenant), f"the run without the poisoned tenant did nothing for {tenant}"
        assert poisoned.calls.get(tenant) == alone.calls[tenant], (
            f"{poison.value} in {POISONED} changed {tenant}'s calls"
        )
