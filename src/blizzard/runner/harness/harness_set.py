"""A runner's selected harness collaborators and their replacement lifetime."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.binding_selection import BindingSelection
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.capability_snapshot import HarnessVersionCache
from blizzard.runner.harness.declaration import HarnessSection
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryPlan
from blizzard.runner.harness.health_cache import HarnessHealthCache
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.process.child_env import AllowlistedEnv


@domain_model
@dataclass(frozen=True)
class HarnessAssemblyInputs:
    """Values passed inward from composition, independent of configuration and wiring types."""

    root: Path
    autonomy: Autonomy
    config_dir: Path | None
    worker_env: AllowlistedEnv
    transcripts_root: str
    sections: tuple[HarnessSection, ...]
    runner_id: str
    telemetry_enabled: bool
    runner_environ: tuple[tuple[str, str], ...] = field(repr=False)
    publish: bool = False
    bundle: BundleSnapshot | None = None


@domain_model
@dataclass(frozen=True)
class HarnessAssemblyKey:
    """Values supplied by the composer that determine an assembly.

    Identities are supplied by the boundary that observes binaries and bundle sources;
    this value performs no I/O itself.
    """

    values: tuple[object, ...]
    binary_identities: tuple[object, ...] = ()
    bundle_source_identity: object = None


@dataclass(frozen=True)
class HarnessSet:
    selection: BindingSelection
    bundle: BundleSnapshot | None
    registry: HarnessRegistry
    probes: Mapping[str, IHarnessHealthProbe]
    health: HarnessHealthCache
    telemetry: HarnessTelemetryPlan
    versions: HarnessVersionCache


class IInvocationHarnesses(Protocol):
    """Ownership of the set selected for an individual in-flight invocation.

    The key identifies the invocation, not its harness session: two concurrent
    invocations can resume the same session. A caller can retain a provisional
    key before launch and transfer it to the process identity once known.
    """

    def retain(self, invocation: Hashable) -> HarnessSet: ...

    def retain_for(self, invocation: Hashable, source: Hashable) -> HarnessSet: ...

    @property
    def current(self) -> HarnessSet: ...

    def lookup(self, invocation: Hashable) -> HarnessSet: ...

    def transfer(self, invocation: Hashable, process: Hashable) -> None: ...

    def release(self, invocation: Hashable) -> None: ...

    def find(self, invocation: Hashable) -> HarnessSet | None: ...

    def worker_for_lease(self, lease_id: str) -> HarnessSet | None: ...

    def release_lease(self, lease_id: str) -> None: ...

    def retire_judge(self, lease_id: str, generation: int) -> None: ...

    def release_judges(self, lease_id: str, generation: int) -> None: ...


@domain_model
@dataclass(frozen=True)
class WorkerInvocation:
    """An in-memory worker invocation, including identity-pending launches."""

    lease_id: str
    generation: int


@domain_model
@dataclass(frozen=True)
class JudgeInvocation:
    """A judge in a generation; zero identifies its currently active invocation."""

    lease_id: str
    generation: int
    token: int = 0


class HarnessSets:
    """Keep only the last assembly and any sets held by active invocations."""

    def __init__(self, build: Callable[[HarnessAssemblyKey], HarnessSet]) -> None:
        self._build = build
        self._key: HarnessAssemblyKey | None = None
        self._current: HarnessSet | None = None
        self._retained: dict[Hashable, HarnessSet] = {}

    def select(self, key: HarnessAssemblyKey) -> HarnessSet:
        if self._current is None or key != self._key:
            selected = self._build(key)
            self._key, self._current = key, selected
        return self._current

    @property
    def current(self) -> HarnessSet:
        return self._selected()

    def _selected(self) -> HarnessSet:
        if self._current is None:
            raise RuntimeError("harness set has not been selected")
        return self._current

    def retain(self, invocation: Hashable) -> HarnessSet:
        selected = self.current
        if invocation in self._retained:
            raise ValueError("invocation already retained")
        self._retained[invocation] = selected
        return selected

    def retain_for(self, invocation: Hashable, source: Hashable) -> HarnessSet:
        """Retain the source invocation's set, or the current set after restart."""
        if invocation in self._retained:
            raise ValueError("invocation already retained")
        selected = self._retained.get(source, self.current)
        self._retained[invocation] = selected
        return selected

    def lookup(self, invocation: Hashable) -> HarnessSet:
        return self._retained[invocation]

    def find(self, invocation: Hashable) -> HarnessSet | None:
        """Find an in-process owner; absence also covers recovery after restart."""
        return self._retained.get(invocation)

    def worker_for_lease(self, lease_id: str) -> HarnessSet | None:
        """The latest retained worker for a lease, without a store read."""
        keys = (key for key in self._retained if isinstance(key, WorkerInvocation) and key.lease_id == lease_id)
        latest = max(keys, key=lambda key: key.generation, default=None)
        return self._retained[latest] if latest is not None else None

    def release_lease(self, lease_id: str) -> None:
        """Release a closed lease's invocations after terminal work."""
        for key in tuple(self._retained):
            if isinstance(key, (WorkerInvocation, JudgeInvocation)) and key.lease_id == lease_id:
                self.release(key)

    def retire_judge(self, lease_id: str, generation: int) -> None:
        """Keep a superseded judge until its cohort's final processing finishes."""
        active = JudgeInvocation(lease_id, generation)
        if active in self._retained:
            self.transfer(active, JudgeInvocation(lease_id, generation, uuid4().int))

    def release_judges(self, lease_id: str, generation: int) -> None:
        """Release every invocation contributing to a completed judge cohort."""
        for key in tuple(self._retained):
            if isinstance(key, JudgeInvocation) and key.lease_id == lease_id and key.generation == generation:
                self.release(key)

    def clear(self) -> None:
        """Forget in-memory invocation ownership when the runner has stopped."""
        self._retained.clear()

    def transfer(self, invocation: Hashable, process: Hashable) -> None:
        """Move provisional ownership to the launched process without reselecting."""
        if process in self._retained:
            raise ValueError("invocation already retained")
        self._retained[process] = self._retained.pop(invocation)

    def release(self, invocation: Hashable) -> None:
        del self._retained[invocation]
