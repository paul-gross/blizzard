"""The crash-point registry (``bzh:crash-point-registry``).

A crash point is declared at module scope beside the boundary it guards — the
declaration is the registration — and ``reached()`` at that boundary SIGKILLs the
process iff it is armed (``BLIZZARD_CRASH_POINT``) and fenced
(``BLIZZARD_CRASH_FENCE=1``). Arming is read once, at import."""

from __future__ import annotations

import importlib
import os
import signal
from dataclasses import dataclass
from typing import ClassVar

#: Names the armed crash point; a kill fires only with the fence below also set.
ENV_CRASH_POINT = "BLIZZARD_CRASH_POINT"
#: The fence (``bzh:crash-point-registry``): a stray point name alone never kills.
ENV_CRASH_FENCE = "BLIZZARD_CRASH_FENCE"

#: The modules that declare crash points; importing them populates the registry.
_INSTRUMENTED_MODULES = (
    "blizzard.runner.loop.steps",
    "blizzard.runner.loop.spawn",
    "blizzard.runner.loop.attempt",
    "blizzard.runner.loop.judgement",
    "blizzard.runner.loop.drain",
    "blizzard.runner.loop.transcript_drain",
    "blizzard.runner.loop.claim",
    "blizzard.runner.loop.dormant",
    "blizzard.runner.domain.attachments",
    "blizzard.runner.domain.git_commit_declaration",
    "blizzard.hub.delivery.hub_node",
    "blizzard.hub.domain.claim",
    "blizzard.hub.domain.apply",
    "blizzard.hub.domain.work_closure",
)


@dataclass(frozen=True)
class Arming:
    """Which point the environment arms, and whether the test fence is set."""

    point: str | None
    fenced: bool

    @classmethod
    def from_env(cls) -> Arming:
        return cls(point=os.environ.get(ENV_CRASH_POINT) or None, fenced=os.environ.get(ENV_CRASH_FENCE) == "1")

    def fires(self, name: str) -> bool:
        """True iff ``name`` is the armed point and the fence permits the kill."""
        return self.point is not None and name == self.point and self.fenced


@dataclass(frozen=True)
class CrashPoint:
    """A named dangerous window. Declaring one registers it; ``reached()`` is the boundary."""

    name: str
    description: str

    _registry: ClassVar[dict[str, CrashPoint]] = {}
    #: Read once, at import — a fresh subprocess per armed run.
    _arming: ClassVar[Arming] = Arming.from_env()

    @classmethod
    def declare(cls, name: str, description: str = "") -> CrashPoint:
        """Declare and register a crash point. Call at module scope beside its boundary."""
        if name in cls._registry:
            raise ValueError(f"duplicate crash point {name!r}")
        point = cls(name=name, description=description)
        cls._registry[name] = point
        return point

    @classmethod
    def all(cls) -> list[CrashPoint]:
        """Every registered crash point, name-sorted — only those whose module is imported."""
        return sorted(cls._registry.values(), key=lambda p: p.name)

    @classmethod
    def discover(cls) -> list[CrashPoint]:
        """Import the instrumented modules, then return every registered crash point."""
        for module in _INSTRUMENTED_MODULES:
            importlib.import_module(module)
        return cls.all()

    @classmethod
    def rearm_from_env(cls) -> None:
        """Re-read the arming from the environment — for in-process unit tests only.

        Every armed run is a fresh subprocess whose import-time read is authoritative."""
        cls._arming = Arming.from_env()

    def reached(self) -> None:
        """At this boundary: SIGKILL the process iff this point is armed and fenced."""
        if not self._arming.fires(self.name):
            return
        # Faithful kill -9: uncatchable, no atexit/finally, no buffered-output flush —
        # the store is left exactly as durable writes left it, which is the whole point.
        os.kill(os.getpid(), signal.SIGKILL)


#: The registry's module-level surface — the declaration verb and the two enumerations.
crashpoint = CrashPoint.declare
all_points = CrashPoint.all
discover_crash_points = CrashPoint.discover
