"""The poisoned-tenant fixture (``bzh:pass-isolates-tenants``): runs a hub-wide pass twice on the virtual clock —
once with one tenant poisoned, once without that tenant — and fails when any other tenant's recorded calls differ.
A pass's builder wires it over fakes that log each call into a tenant's half through the :class:`Recorder`, and
returns its ``sweep``; a reconciler with a one-tenant pass is also run with the poisoned tenant's one-tenant passes
interleaved between the hub-wide ones."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Protocol

from blizzard.foundation.clock import FixedClock
from blizzard.hub.tenancy import TenantId

#: Sorts before, and leads the corpus ahead of, the healthy tenants, so a leak reaches them.
POISONED = TenantId("ten_a")
HEALTHY: tuple[TenantId, ...] = (TenantId("ten_b"), TenantId("ten_c"))

START = datetime(2026, 7, 13, tzinfo=UTC)
INTERVAL = timedelta(minutes=1)


class Poison(Enum):
    RAISES = "raises"
    UNCONVERGED = "unconverged"
    BURST = "burst"


@dataclass(frozen=True)
class Scene:
    poisoned: TenantId | None
    poison: Poison | None

    @property
    def tenants(self) -> tuple[TenantId, ...]:
        return (*HEALTHY, *([self.poisoned] if self.poisoned else []))


class Recorder:
    def __init__(self) -> None:
        self.pass_no = 0
        self.calls: dict[TenantId, list[tuple[object, ...]]] = {}

    def log(self, tenant: TenantId, *call: object) -> None:
        self.calls.setdefault(tenant, []).append((self.pass_no, *call))


Build = Callable[[Scene, Recorder, FixedClock], Callable[[], None]]


class SweepsOneTenant(Protocol):
    def sweep(self) -> None: ...

    def sweep_tenant(self, tenant: TenantId) -> object: ...


BuildSweepsOneTenant = Callable[[Scene, Recorder, FixedClock], SweepsOneTenant]


def _run(build: Build, scene: Scene, passes: int, *, between: Callable[[], object] | None = None) -> Recorder:
    recorder, clock = Recorder(), FixedClock(START)
    sweep = build(scene, recorder, clock)
    for number in range(passes):
        recorder.pass_no = number
        sweep()
        if between is not None:
            between()
        clock.advance(INTERVAL)
    return recorder


def assert_pass_isolates(build: Build, poison: Poison, *, passes: int = 14) -> None:
    """Every healthy tenant's calls are identical with ``POISONED`` poisoned and without it."""
    poisoned = _run(build, Scene(POISONED, poison), passes)
    alone = _run(build, Scene(None, None), passes)
    for tenant in HEALTHY:
        assert alone.calls.get(tenant), f"the run without the poisoned tenant did nothing for {tenant}"
        assert poisoned.calls.get(tenant) == alone.calls[tenant], (
            f"{poison.value} in {POISONED} changed {tenant}'s calls"
        )


def assert_tenant_pass_isolates(build: BuildSweepsOneTenant, poison: Poison, *, passes: int = 14) -> None:
    """Every healthy tenant's calls are identical with ``POISONED`` poisoned and its one-tenant passes run between
    the hub-wide ones, and without ``POISONED`` at all."""
    built: list[SweepsOneTenant] = []

    def hub_wide(scene: Scene, recorder: Recorder, clock: FixedClock) -> Callable[[], None]:
        built.append(build(scene, recorder, clock))
        return built[-1].sweep

    poisoned = _run(hub_wide, Scene(POISONED, poison), passes, between=lambda: built[-1].sweep_tenant(POISONED))
    assert poisoned.calls.get(POISONED), f"no one-tenant pass reached {POISONED}"
    alone = _run(build_sweep(build), Scene(None, None), passes)
    for tenant in HEALTHY:
        assert alone.calls.get(tenant), f"the run without the poisoned tenant did nothing for {tenant}"
        assert poisoned.calls.get(tenant) == alone.calls[tenant], (
            f"{poison.value} in {POISONED}'s one-tenant passes changed {tenant}'s calls"
        )


def build_sweep(build: BuildSweepsOneTenant) -> Build:
    """``build``'s hub-wide pass alone."""
    return lambda scene, recorder, clock: build(scene, recorder, clock).sweep
