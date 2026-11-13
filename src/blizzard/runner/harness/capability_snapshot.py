"""This runner's capability snapshots and version evidence; ``bzh:pluggable-seams`` owns binding lifetime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.binary_identity import BinaryIdentity, IBinaryIdentity
from blizzard.runner.harness.health import reported_health
from blizzard.runner.harness.health_cache import HARNESS_VERSION_REFRESH_SECONDS, HarnessHealthCache
from blizzard.runner.harness.registry import (
    IHarnessKnownRegistry,
    IHarnessLifecycleRegistry,
    IHarnessModelResolutionRegistry,
)


@domain_model
@dataclass(frozen=True)
class HarnessCapability:
    """One harness binding this runner can execute — its id, its observed version (``None`` when
    the binding exposes none), the tier ids it resolves, the native model names or patterns it supports,
    whether it is the runner's default, and whether it is available."""

    harness_id: str
    version: str | None = None
    tiers: list[str] = field(default_factory=list[str])
    supported_models: list[str] = field(default_factory=list[str])
    default: bool = False
    available: bool = True


@dataclass
class BinaryVersionCache:
    """Successful version evidence shared by probes of the same executable identity.

    Identity is read again after observation so a binary replaced during the probe cannot
    attach the old result to the new file. A missing identity or failed observation is
    never reusable. Service observations are deliberately outside this cache.
    """

    identities: IBinaryIdentity
    _observed: dict[str, tuple[BinaryIdentity, str]] = field(default_factory=dict[str, tuple[BinaryIdentity, str]])
    _paths: dict[str, str] = field(default_factory=dict[str, str])

    def with_identities(self, identities: IBinaryIdentity) -> BinaryVersionCache:
        """Share file evidence while resolving names under another tool profile."""
        return BinaryVersionCache(identities=identities, _observed=self._observed)

    def get(self, binary: str, observe: Callable[[], str | None]) -> str | None:
        identity = self.identities.identify(binary)
        previous = self._paths.get(binary)
        path = str(identity.path) if identity is not None else None
        if previous is not None and previous != path:
            self._observed.pop(previous, None)
            self._paths.pop(binary, None)
        if identity is not None:
            self._paths[binary] = str(identity.path)
            cached = self._observed.get(str(identity.path))
            if cached is not None and cached[0] == identity:
                return cached[1]
        version = observe()
        if identity is not None and version and self.identities.identify(binary) == identity:
            self._observed[str(identity.path)] = (identity, version)
        return version

    def forget(self, binary: str) -> None:
        """Invalidate successful evidence for the executable currently named by ``binary``."""
        identity = self.identities.identify(binary)
        path = str(identity.path) if identity is not None else self._paths.get(binary)
        if path is not None:
            self._observed.pop(path, None)


@dataclass
class HarnessVersionCache:
    """Cache service observations independently from shared binary-version evidence.

    Binary probes are cached below lifecycle observations, so an adapter is called on
    every read unless it has separately declared an answering service. The latter keeps
    its refresh cadence, but a changed or missing binary invalidates its observation.
    """

    clock: IClock
    refresh_seconds: float = HARNESS_VERSION_REFRESH_SECONDS
    binary_versions: BinaryVersionCache | None = None
    binaries: Mapping[str, str] = field(default_factory=dict[str, str])
    service_harnesses: frozenset[str] = frozenset()
    _observed: dict[str, tuple[str | None, datetime, BinaryIdentity]] = field(
        default_factory=dict[str, tuple[str | None, datetime, BinaryIdentity]], compare=False
    )

    def get(self, harness_id: str, observe: Callable[[], str | None]) -> str | None:
        binary = self.binaries.get(harness_id)
        if harness_id not in self.service_harnesses or binary is None or self.binary_versions is None:
            return observe()
        identity = self.binary_versions.identities.identify(binary)
        if identity is None:
            self._observed.pop(harness_id, None)
            return observe()
        now = self.clock.now()
        cached = self._observed.get(harness_id)
        if cached is not None and cached[2] == identity and (now - cached[1]).total_seconds() < self.refresh_seconds:
            return cached[0]
        version = observe()
        if self.binary_versions.identities.identify(binary) == identity:
            self._observed[harness_id] = (version, now, identity)
        return version

    def forget(self, harness_id: str) -> None:
        """Drop ``harness_id``'s cached version, so the next read observes it afresh."""
        self._observed.pop(harness_id, None)
        binary = self.binaries.get(harness_id)
        if binary is not None and self.binary_versions is not None:
            self.binary_versions.forget(binary)


class CapabilityHarnesses(IHarnessKnownRegistry, IHarnessLifecycleRegistry, IHarnessModelResolutionRegistry, Protocol):
    """What the capability snapshot reads of the registry: the known ids, each one's version probe and model tiers."""


def default_harness_id(harnesses: IHarnessKnownRegistry) -> str | None:
    """The runner's own default harness — the registry's own binding order
    decides which one that is, with no separate config key. ``None`` only for the legacy
    no-bindings registry some tests construct. The one place this is decided."""
    known = harnesses.known_harnesses
    return known[0] if known else None


def capability_snapshot(
    harnesses: CapabilityHarnesses,
    versions: HarnessVersionCache | None = None,
    health: HarnessHealthCache | None = None,
) -> tuple[HarnessCapability, ...]:
    """One entry per known harness binding, each carrying the tier ids its adapter can resolve, its observed
    version, and its computed availability. The entry matching :func:`default_harness_id` is marked
    ``default``. ``versions`` routes the version probe through the cross-tick cache when wired; omitted, this probes
    directly (a one-shot caller with no "next tick" a cache would pay off). ``health`` omitted reports every entry
    unprobed (:func:`~blizzard.runner.harness.health.reported_health`): a caller with no health cache asserts none."""
    default_id = default_harness_id(harnesses)
    snapshot: list[HarnessCapability] = []
    for harness_id in harnesses.known_harnesses:
        observe_version = harnesses.lifecycle(harness_id).observe_version
        model = harnesses.model_resolution(harness_id)
        version = versions.get(harness_id, observe_version) if versions is not None else observe_version()
        refreshed = health.refresh(harness_id, adapter=model, observed_version=version) if health is not None else None
        result = reported_health(harness_id, refreshed)
        snapshot.append(
            HarnessCapability(
                harness_id=harness_id,
                version=version,
                tiers=list(model.resolvable_tier_ids()),
                supported_models=list(model.supported_models()),
                default=harness_id == default_id,
                available=result.available,
            )
        )
    return tuple(snapshot)


@dataclass
class TickCapabilities:
    """One tick's own snapshot, built at most once — the per-tick memo shape ``chunk_status_cache`` already gives
    chunk reads. ``versions`` (the composition root's long-lived :class:`HarnessVersionCache`) is what bounds the
    cost *across* ticks; this memo alone only bounds one tick's own repeat calls."""

    harnesses: CapabilityHarnesses
    versions: HarnessVersionCache | None = None
    health: HarnessHealthCache | None = None
    _snapshot: tuple[HarnessCapability, ...] | None = field(default=None, compare=False)

    def get(self) -> tuple[HarnessCapability, ...]:
        if self._snapshot is None:
            self._snapshot = capability_snapshot(self.harnesses, self.versions, self.health)
        return self._snapshot
