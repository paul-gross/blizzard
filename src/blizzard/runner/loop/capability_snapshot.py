"""This runner's own capability snapshot (blizzard#433) — shared by every outbound call
that carries one: the registration push (``steps.py``'s ``Pull._sync_registry``) and the
matched fleet peek (``claim.py``'s ``ReadyQueue.peeked``). A free function rather than a
method on either caller's own module, since ``steps.py`` imports ``claim.py`` — a method
on one would make the other's use of it circular."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.wire.runner import RunnerCapability

#: How long a version is trusted before re-probing — long enough an idle runner isn't shelling out every tick.
HARNESS_VERSION_REFRESH_SECONDS = 600.0


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
    """The runner's own default harness (blizzard#433) — the registry's own binding order
    decides which one that is, with no separate config key. ``None`` only for the legacy
    no-bindings registry some tests construct. The one place this is decided; both
    ``capability_snapshot`` and ``spawn.py``'s no-``session_harnesses`` fallback defer here."""
    known = harnesses.known_harnesses
    return known[0] if known else None


def capability_snapshot(
    harnesses: IHarnessRegistry, versions: HarnessVersionCache | None = None
) -> tuple[RunnerCapability, ...]:
    """One entry per known harness binding, each carrying the tier ids its adapter can resolve and its observed
    version. The entry matching :func:`default_harness_id` is marked ``default``. ``versions`` routes the version
    probe through the cross-tick cache when wired; omitted, this probes directly (a one-shot caller with no "next
    tick" a cache would pay off)."""
    default_id = default_harness_id(harnesses)
    snapshot: list[RunnerCapability] = []
    for harness_id in harnesses.known_harnesses:
        adapter = harnesses.adapter(harness_id)
        version = (
            versions.get(harness_id, adapter.observe_version) if versions is not None else adapter.observe_version()
        )
        snapshot.append(
            RunnerCapability(
                harness_id=harness_id,
                version=version,
                tiers=list(adapter.resolvable_tier_ids()),
                default=harness_id == default_id,
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
    _snapshot: tuple[RunnerCapability, ...] | None = field(default=None, compare=False)

    def get(self) -> tuple[RunnerCapability, ...]:
        if self._snapshot is None:
            self._snapshot = capability_snapshot(self.harnesses, self.versions)
        return self._snapshot
