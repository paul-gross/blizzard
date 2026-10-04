"""This runner's own capability snapshot — shared by every outbound call
that carries one: the registration push (``steps.py``'s ``Pull._sync_registry``) and the
matched fleet peek (``claim.py``'s ``ReadyQueue.peeked``). A free function rather than a
method on either caller's own module, since ``steps.py`` imports ``claim.py`` — a method
on one would make the other's use of it circular."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.runner.harness.health_cache import HARNESS_VERSION_REFRESH_SECONDS, HarnessHealthCache
from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.wire.runner import RunnerCapability


@dataclass
class HarnessVersionCache:
    """Every bound harness's last-observed binary version, cached **across ticks** (unlike
    :class:`TickCapabilities`' own per-tick memo below), composition-root-owned and as
    long-lived as the loop itself — so an idle runner with a harness installed no longer
    pays ``observe_version``'s subprocess cost on every tick, only once per refresh window."""

    clock: IClock
    refresh_seconds: float = HARNESS_VERSION_REFRESH_SECONDS
    _observed: dict[str, tuple[str | None, datetime]] = field(default_factory=dict, compare=False)

    def get(self, harness_id: str, observe: Callable[[], str | None]) -> str | None:
        now = self.clock.now()
        cached = self._observed.get(harness_id)
        if cached is not None and (now - cached[1]).total_seconds() < self.refresh_seconds:
            return cached[0]
        version = observe()
        self._observed[harness_id] = (version, now)
        return version


def default_harness_id(harnesses: IHarnessRegistry) -> str | None:
    """The runner's own default harness — the registry's own binding order
    decides which one that is, with no separate config key. ``None`` only for the legacy
    no-bindings registry some tests construct. The one place this is decided; both
    ``capability_snapshot`` and ``lifecycle/spawn.py``'s no-``session_harnesses`` fallback defer here."""
    known = harnesses.known_harnesses
    return known[0] if known else None


def capability_snapshot(
    harnesses: IHarnessRegistry,
    versions: HarnessVersionCache | None = None,
    health: HarnessHealthCache | None = None,
) -> tuple[RunnerCapability, ...]:
    """One entry per known harness binding, each carrying the tier ids its adapter can resolve, its observed
    version, and its computed availability. The entry matching :func:`default_harness_id` is marked
    ``default``. ``versions`` routes the version probe through the cross-tick cache when wired; omitted, this probes
    directly (a one-shot caller with no "next tick" a cache would pay off). ``health`` omitted defaults every entry
    ``available=True`` — a caller with no health cache wired asserts none, matching the wire's own default."""
    default_id = default_harness_id(harnesses)
    snapshot: list[RunnerCapability] = []
    for harness_id in harnesses.known_harnesses:
        observe_version = harnesses.lifecycle(harness_id).observe_version
        model = harnesses.model_resolution(harness_id)
        version = versions.get(harness_id, observe_version) if versions is not None else observe_version()
        result = health.refresh(harness_id, adapter=model, observed_version=version) if health is not None else None
        snapshot.append(
            RunnerCapability(
                harness_id=harness_id,
                version=version,
                tiers=list(model.resolvable_tier_ids()),
                default=harness_id == default_id,
                available=result.available if result is not None else True,
            )
        )
    return tuple(snapshot)


@dataclass
class TickCapabilities:
    """One tick's own snapshot, built at most once — the per-tick memo shape ``chunk_status_cache`` already gives
    chunk reads. ``versions`` (the composition root's long-lived :class:`HarnessVersionCache`) is what bounds the
    cost *across* ticks; this memo alone only bounds one tick's own repeat calls."""

    harnesses: IHarnessRegistry
    versions: HarnessVersionCache | None = None
    health: HarnessHealthCache | None = None
    _snapshot: tuple[RunnerCapability, ...] | None = field(default=None, compare=False)

    def get(self) -> tuple[RunnerCapability, ...]:
        if self._snapshot is None:
            self._snapshot = capability_snapshot(self.harnesses, self.versions, self.health)
        return self._snapshot
