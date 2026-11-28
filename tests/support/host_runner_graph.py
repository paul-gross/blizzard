"""A test-owned runner graph over the real host root, and the fake runners, wiring and resume hooks that close in it."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, cast

from blizzard.foundation.registry.interface import Lifetime
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild
from blizzard.runner.composition import HostProcess, IRunnerDrivers, LiveRunner, RunnerProcess
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import BuiltRunner, HaltedRunner, HostConfig
from blizzard.runner.host_root import HostRoot, register_host


@dataclass
class Journal:
    """What opened and closed, in order."""

    entries: list[str] = field(default_factory=list[str])
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def note(self, entry: str) -> None:
        with self._lock:
            self.entries.append(entry)

    def of(self, runner_id: str) -> list[str]:
        return [entry for entry in self.entries if entry.endswith(f":{runner_id}")]


class HostResource:
    """A process-lifetime resource of the test's graph."""


class RunnerService:
    """A runner-lifetime service; its constructions are noted."""

    def __init__(self, built: BuiltRunner, shared: HostResource, host: HostProcess, journal: Journal) -> None:
        journal.note(f"built:{built.id}")
        self.built = built
        self.shared = shared
        self.host = host


class RunnerResource:
    """A runner-lifetime resource whose finalizer is the child's closure."""


def _open_host_resource(journal: Journal) -> Iterator[HostResource]:
    try:
        yield HostResource()
    finally:
        journal.note("close:host-resource")


def _open_runner_resource(built: BuiltRunner, journal: Journal) -> Iterator[RunnerResource]:
    try:
        yield RunnerResource()
    finally:
        journal.note(f"child:{built.id}")


@dataclass
class Borrowed:
    """The host the root borrows: only what the runner set reads and writes, and a close it must never call."""

    journal: Journal
    runners: tuple[RunnerProcess, ...] = ()
    halted: tuple[HaltedRunner, ...] = ()

    def close(self) -> None:
        self.journal.note("close:borrowed-host")


@dataclass
class FakeRunner:
    runner_id: str
    journal: Journal

    def close_runner(self) -> None:
        self.journal.note(f"graph:{self.runner_id}")


@dataclass
class FakeWiring:
    runner_id: str
    journal: Journal

    def quiesce(self) -> None:
        return None

    def close(self) -> None:
        self.journal.note(f"wiring:{self.runner_id}")


class FakeResume:
    def on_startup(self) -> int:
        return 0

    def recover_takeovers(self) -> int:
        return 0

    def reconcile_sessions(self) -> int:
        return 0

    def on_shutdown(self) -> int:
        return 0


@dataclass
class Rig:
    journal: Journal
    host: Borrowed
    root: HostRoot[FakeWiring, FakeResume]

    def open(self, runner_id: str) -> OwnedChild:
        child = self.root.runners.open_runner(built_runner(runner_id))
        assert child is not None
        return child

    def place(self, runner_id: str, drivers: IRunnerDrivers | None = None) -> OwnedChild:
        """One served runner over a child of its own."""
        child = self.open(runner_id)
        runner = cast("RunnerProcess", FakeRunner(runner_id, self.journal))
        wired = FakeWiring(runner_id, self.journal)
        self.root.runners.place(LiveRunner(runner, FakeResume(), wired=wired, drivers=drivers, child=child))
        return child


def built_runner(runner_id: str) -> BuiltRunner:
    return BuiltRunner(runner_id, cast("RunnerConfig", object()))


def host_rig(
    *,
    eager: tuple[type[Any], ...] = (RunnerResource,),
    extra: Callable[[RecordingRegistry], None] | None = None,
) -> Rig:
    """The host root over a journalled graph; ``extra`` adds registrations before the build."""
    journal = Journal()
    registry = LegacyRegistry()
    register_host(registry)

    def provide_journal() -> Journal:
        return journal

    registry.factory(provide_journal, lifetime=Lifetime.PROCESS)
    registry.resource(_open_host_resource, lifetime=Lifetime.PROCESS)
    registry.service(RunnerService, lifetime=Lifetime.RUNNER)
    registry.resource(_open_runner_resource, lifetime=Lifetime.RUNNER)
    if extra is not None:
        extra(registry)
    host = Borrowed(journal)
    root = HostRoot[FakeWiring, FakeResume].open(
        registry, cast("HostProcess", host), cast("HostConfig", object()), eager=eager
    )
    return Rig(journal, host, root)
