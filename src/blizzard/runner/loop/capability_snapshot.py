"""This runner's own capability snapshot (blizzard#433) — shared by every outbound call
that carries one: the registration push (``steps.py``'s ``Pull._sync_registry``) and the
matched fleet peek (``claim.py``'s ``ReadyQueue.peeked``). A free function rather than a
method on either caller's own module, since ``steps.py`` imports ``claim.py`` — a method
on one would make the other's use of it circular."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.runner.domain.selftest_result import IReadSelfTestResultRepository
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.health import HarnessHealthEvidence, HarnessHealthResult, evaluate_harness_health
from blizzard.runner.harness.internal.offline_compatibility import classify_offline
from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.wire.runner import RunnerCapability


class _ResolvesModelStrict(Protocol):
    """The one sliver of :class:`~blizzard.runner.harness.adapter.IHarnessModelResolution`
    the unmapped-tier check needs (``bzh:seam-size-ceiling``) — a full adapter's own
    ``resolve_model_strict`` already satisfies this."""

    def resolve_model_strict(self, preferences: Sequence[str]) -> str | None: ...


#: How long a version is trusted before re-probing — long enough an idle runner isn't shelling out every tick.
HARNESS_VERSION_REFRESH_SECONDS = 600.0

#: Distinguishes "never computed" from a real, if coincidentally ``None``-valued, previous
#: observation — a sentinel a stored ``None`` cannot be confused with.
_UNSET = object()


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


def _unmapped_tiers(adapter: _ResolvesModelStrict, declared: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """Every tier the operator configured this harness to resolve (``declared``, the raw
    alias table off ``RunnerConfig``) that the adapter itself cannot actually resolve —
    e.g. a declared alias mapped to an empty native model. Empty for a harness declaring
    no aliases at all, which asserts no configured tier to fail this check."""
    return tuple(tier for tier, _ in declared if adapter.resolve_model_strict((tier,)) is None)


@dataclass
class HarnessHealthCache:
    """Every configured harness binding's last-computed health result (blizzard#438),
    held across ticks like :class:`HarnessVersionCache` — health evidence includes
    subprocess and credential probes, so recomputing on every peek would put that cost
    there instead of on this cache's own bounded refresh window.

    Recomputes when the refresh window elapses, the observed version changes, or a new
    selftest result lands (the three occasions the plan names) — never merely because a
    peek asked. The very first call for a harness always computes: an empty cache is
    always stale, which is what covers "at daemon start" with no separate startup hook."""

    clock: IClock
    probes: Mapping[str, IHarnessHealthProbe]
    #: ``None`` on a store-free composition (the OpenAPI exporter, a unit test) — a
    #: never-run selftest either way, since neither can have recorded one.
    selftest_results: IReadSelfTestResultRepository | None
    #: Per-harness declared (tier, native-model) pairs off ``RunnerConfig`` — the tiers
    #: this runner is configured to resolve *through this harness specifically*.
    configured_tiers: Mapping[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    refresh_seconds: float = HARNESS_VERSION_REFRESH_SECONDS
    _results: dict[str, HarnessHealthResult] = field(default_factory=dict, compare=False)
    _computed_at: dict[str, datetime] = field(default_factory=dict, compare=False)
    _last_version: dict[str, object] = field(default_factory=dict, compare=False)
    _last_selftest: dict[str, object] = field(default_factory=dict, compare=False)

    def refresh(
        self, harness_id: str, *, adapter: _ResolvesModelStrict, observed_version: str | None
    ) -> HarnessHealthResult | None:
        """Recompute (or reuse) ``harness_id``'s health; ``None`` for a harness this
        composition wired no health probe for."""
        probe = self.probes.get(harness_id)
        if probe is None:
            return None
        latest = self.selftest_results.latest_selftest_result(harness_id) if self.selftest_results is not None else None
        selftest_marker = (latest.status, latest.recorded_at) if latest is not None else None
        now = self.clock.now()
        computed_at = self._computed_at.get(harness_id)
        stale = computed_at is None or (now - computed_at).total_seconds() >= self.refresh_seconds
        changed = (
            self._last_version.get(harness_id, _UNSET) != observed_version
            or self._last_selftest.get(harness_id, _UNSET) != selftest_marker
        )
        if not stale and not changed and harness_id in self._results:
            return self._results[harness_id]
        supported_version = probe.supported_version()
        result = evaluate_harness_health(
            HarnessHealthEvidence(
                harness_id=harness_id,
                binary_present=probe.binary_present(),
                version_declared=supported_version is not None,
                version_classification=(
                    classify_offline(harness_id, observed_version) if supported_version is not None else None
                ),
                authenticated=probe.probe_authentication(),
                unmapped_tiers=_unmapped_tiers(adapter, self.configured_tiers.get(harness_id, ())),
                selftest_failed=(latest.status == "failed") if latest is not None else None,
                degradations=probe.declared_degradations(),
            )
        )
        self._results[harness_id] = result
        self._computed_at[harness_id] = now
        self._last_version[harness_id] = observed_version
        self._last_selftest[harness_id] = selftest_marker
        return result

    def get(self, harness_id: str) -> HarnessHealthResult | None:
        """The last-computed result, or ``None`` when :meth:`refresh` has never run for
        this harness — :class:`~blizzard.runner.loop.session.HarnessSelector`'s own read,
        which must never itself trigger a probe mid-selection."""
        return self._results.get(harness_id)


def default_harness_id(harnesses: IHarnessRegistry) -> str | None:
    """The runner's own default harness (blizzard#433) — the registry's own binding order
    decides which one that is, with no separate config key. ``None`` only for the legacy
    no-bindings registry some tests construct. The one place this is decided; both
    ``capability_snapshot`` and ``spawn.py``'s no-``session_harnesses`` fallback defer here."""
    known = harnesses.known_harnesses
    return known[0] if known else None


def capability_snapshot(
    harnesses: IHarnessRegistry,
    versions: HarnessVersionCache | None = None,
    health: HarnessHealthCache | None = None,
) -> tuple[RunnerCapability, ...]:
    """One entry per known harness binding, each carrying the tier ids its adapter can resolve, its observed
    version, and its computed availability (blizzard#438). The entry matching :func:`default_harness_id` is marked
    ``default``. ``versions`` routes the version probe through the cross-tick cache when wired; omitted, this probes
    directly (a one-shot caller with no "next tick" a cache would pay off). ``health`` omitted defaults every entry
    ``available=True`` — a caller with no health cache wired asserts none, matching the wire's own default."""
    default_id = default_harness_id(harnesses)
    snapshot: list[RunnerCapability] = []
    for harness_id in harnesses.known_harnesses:
        adapter = harnesses.adapter(harness_id)
        version = (
            versions.get(harness_id, adapter.observe_version) if versions is not None else adapter.observe_version()
        )
        result = health.refresh(harness_id, adapter=adapter, observed_version=version) if health is not None else None
        snapshot.append(
            RunnerCapability(
                harness_id=harness_id,
                version=version,
                tiers=list(adapter.resolvable_tier_ids()),
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
