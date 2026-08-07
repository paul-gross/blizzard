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

#: Names the armed crash point; a kill fires only with the fence below also set.
ENV_CRASH_POINT = "BLIZZARD_CRASH_POINT"
#: The fence (``bzh:crash-point-registry``): a stray point name alone never kills.
ENV_CRASH_FENCE = "BLIZZARD_CRASH_FENCE"


@dataclass(frozen=True)
class CrashPoint:
    """A named dangerous window. Declaring one registers it; ``reached()`` is the boundary."""

    name: str
    description: str

    def reached(self) -> None:
        """At this boundary: SIGKILL the process iff this point is armed and fenced."""
        if _ARMED_POINT is None or self.name != _ARMED_POINT:
            return
        if not _FENCED:  # never fire outside test scaffolding
            return
        # Faithful kill -9: uncatchable, no atexit/finally, no buffered-output flush —
        # the store is left exactly as durable writes left it, which is the whole point.
        os.kill(os.getpid(), signal.SIGKILL)


_registry: dict[str, CrashPoint] = {}


def crashpoint(name: str, description: str = "") -> CrashPoint:
    """Declare and register a crash point. Call at module scope beside its boundary."""
    if name in _registry:
        raise ValueError(f"duplicate crash point {name!r}")
    point = CrashPoint(name=name, description=description)
    _registry[name] = point
    return point


def all_points() -> list[CrashPoint]:
    """Every registered crash point, name-sorted — only those whose module is imported."""
    return sorted(_registry.values(), key=lambda p: p.name)


#: The modules that declare crash points; importing them populates the registry.
_INSTRUMENTED_MODULES = (
    "blizzard.runner.loop.steps",
    "blizzard.runner.loop.spawn",
    "blizzard.runner.loop.attempt",
    "blizzard.runner.loop.judgement",
    "blizzard.runner.domain.attachments",
    "blizzard.runner.domain.git_commit_declaration",
    "blizzard.hub.delivery.hub_node",
    "blizzard.hub.domain.claim",
    "blizzard.hub.domain.apply",
)


def discover_crash_points() -> list[CrashPoint]:
    """Import the instrumented modules, then return every registered crash point."""
    for module in _INSTRUMENTED_MODULES:
        importlib.import_module(module)
    return all_points()


# Read the arming once, at import — a fresh subprocess per armed run.
_ARMED_POINT: str | None = os.environ.get(ENV_CRASH_POINT) or None
_FENCED: bool = os.environ.get(ENV_CRASH_FENCE) == "1"


def _rearm_from_env() -> None:
    """Re-read the arming from the environment — for in-process unit tests only.

    Every armed run is a fresh subprocess whose import-time read is authoritative.
    """
    global _ARMED_POINT, _FENCED
    _ARMED_POINT = os.environ.get(ENV_CRASH_POINT) or None
    _FENCED = os.environ.get(ENV_CRASH_FENCE) == "1"
