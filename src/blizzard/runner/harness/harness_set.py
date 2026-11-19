"""A runner's selected harness collaborators and their replacement lifetime."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass, field, replace
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
from blizzard.runner.harness.registry import (
    HarnessRegistry,
    IHarnessLifecycleAndVerdictRegistry,
    IHarnessLifecycleRegistry,
)
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
    spawn_roots: tuple[str, ...] = ()
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
    #: A recovery-only rebuild keeps the current enabled bindings, rather than reselecting them.
    preserve_selection: bool = field(default=False, compare=False)
    retry_unresolved: bool = field(default=False, compare=False)


@dataclass(frozen=True)
class HarnessSet:
    selection: BindingSelection
    bundle: BundleSnapshot | None
    registry: HarnessRegistry
    probes: Mapping[str, IHarnessHealthProbe]
    health: HarnessHealthCache
    telemetry: HarnessTelemetryPlan
    versions: HarnessVersionCache
    recovery: HarnessSet | None = None
    recovery_only: bool = False
    pending_recovery: frozenset[str] = frozenset()


class IOwnedHarnessRegistry(IHarnessLifecycleRegistry, IHarnessLifecycleAndVerdictRegistry, Protocol):
    """The registry slice a step reaches through an owned set."""


class IOwnedHarnessVersions(Protocol):
    def get(self, harness_id: str, observe: Callable[[], str | None]) -> str | None: ...

    def forget(self, harness_id: str) -> None: ...


class IOwnedHarnessHealth(Protocol):
    def invalidate(self, harness_id: str) -> None: ...


class IOwnedHarnessSet(Protocol):
    """What a step reads off a set it owns; read-only, so the frozen :class:`HarnessSet` satisfies it."""

    @property
    def registry(self) -> IOwnedHarnessRegistry: ...

    @property
    def versions(self) -> IOwnedHarnessVersions: ...

    @property
    def health(self) -> IOwnedHarnessHealth: ...

    @property
    def recovery_only(self) -> bool: ...


class IInvocationHarnesses(Protocol):
    """Ownership of the set selected for an individual in-flight invocation.

    The key identifies the invocation, not its harness session: two concurrent
    invocations can resume the same session.
    """

    def retain(self, invocation: Hashable) -> IOwnedHarnessSet: ...

    def retain_for(self, invocation: Hashable, source: Hashable) -> IOwnedHarnessSet: ...

    @property
    def current(self) -> IOwnedHarnessSet: ...

    def owner(self, lease_id: str, generation: int | None = None, *, judge: bool = False) -> IOwnedHarnessSet: ...

    def release(self, invocation: Hashable) -> None: ...

    def find(self, invocation: Hashable) -> IOwnedHarnessSet | None: ...

    def awaiting_recovery(self, invocation: WorkerInvocation, harness_id: str) -> bool: ...

    def worker_for_lease(self, lease_id: str) -> IOwnedHarnessSet | None: ...

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
        selected = self.prepare(key)
        self.publish(key, selected)
        return selected

    def prepare(self, key: HarnessAssemblyKey) -> HarnessSet:
        """Build without changing the owner used by running or new invocations."""
        if self._current is None or key != self._key:
            built = self._build(key)
            if self._current is not None and key.preserve_selection:
                return replace(self._current, recovery=built.recovery, pending_recovery=built.pending_recovery)
            return built
        return self._current

    def retry_recovery(self, key: HarnessAssemblyKey) -> HarnessSet:
        """Retry unresolved recovery without replacing the enabled runtime."""
        current = self.current
        built = self._build(replace(key, preserve_selection=True, retry_unresolved=True))
        if built.pending_recovery == current.pending_recovery:
            return current
        return replace(current, recovery=built.recovery, pending_recovery=built.pending_recovery)

    def publish(self, key: HarnessAssemblyKey, selected: HarnessSet) -> None:
        self._key, self._current = key, selected

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

    def recover(self, invocation: WorkerInvocation, harness_id: str, selected: HarnessSet) -> None:
        """Anchor a durable session to its available startup binding, without changing admission."""
        if invocation in self._retained:
            return
        owner = selected if harness_id in selected.registry.known_harnesses else selected.recovery
        if owner is not None and harness_id in owner.registry.known_harnesses:
            self._retained[invocation] = owner

    def awaiting_recovery(self, invocation: WorkerInvocation, harness_id: str) -> bool:
        if self.find(invocation) is not None or harness_id in self.current.registry.known_harnesses:
            return False
        inputs = self._key.values[0] if self._key is not None and self._key.values else None
        return isinstance(inputs, HarnessAssemblyInputs) and any(
            section.harness_id == harness_id and not section.enabled for section in inputs.sections
        )

    def retain_for(self, invocation: Hashable, source: Hashable) -> HarnessSet:
        """Retain the source invocation's set, or the current set after restart."""
        if invocation in self._retained:
            raise ValueError("invocation already retained")
        selected = self._retained.get(source, self.current)
        self._retained[invocation] = selected
        return selected

    def lookup(self, invocation: Hashable) -> HarnessSet:
        return self._retained[invocation]

    def owner(self, lease_id: str, generation: int | None = None, *, judge: bool = False) -> HarnessSet:
        """The set retained for an invocation, else the current set.

        With a generation the lookup is exact, a judge falling back to its
        generation's worker; with only a lease, the newest retained worker.
        """
        if generation is None:
            return self.worker_for_lease(lease_id) or self.current
        if judge and (retained := self.find(JudgeInvocation(lease_id, generation))) is not None:
            return retained
        return self.find(WorkerInvocation(lease_id, generation)) or self.current

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
