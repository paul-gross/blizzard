"""The crash-point registry (``bzh:crash-point-registry``).

The dangerous windows of the daemon loops carry stable names in this code-owned,
**enumerable** registry. A crash point is *declared* at module scope right beside the
boundary it guards (so the declaration is the registration — there is no separate
list of names to drift) and *reached* inside the step, exactly at the boundary:

    FILL_AFTER_CLAIM = crashpoint("fill.after-claim.before-bind", "hub holds the route; ...")
    ...
    FILL_AFTER_CLAIM.reached()   # <- the boundary

When a point is **armed** — its name in ``BLIZZARD_CRASH_POINT`` and the test fence
``BLIZZARD_CRASH_FENCE=1`` both set on the process — reaching it SIGKILLs the process
on the spot: a faithful ``kill -9`` (uncatchable, no Python finalizers, no stdio
flush), which is precisely what the kill-9 sweep needs to exercise recovery. Unarmed,
``reached()`` is a single module-global string compare — zero meaningful overhead, and
nothing ever fires.

**Self-fencing** (the same convention as the mock harness): the kill fires only when
``BLIZZARD_CRASH_FENCE=1`` is set, so an accidental ``BLIZZARD_CRASH_POINT`` in a
production environment can never terminate a real daemon. The arming is read **once at
import** — every armed run is a fresh subprocess, so there is no re-arm-mid-process
case to serve, and the read stays off the hot path.

The sweep enumerates the registry via :func:`discover_crash_points` (which imports the
instrumented loop modules, then returns :func:`all_points`) — it never hard-codes the
point list, so a newly-introduced window is a registry entry the sweep picks up, not a
silent gap (``bzh:crash-point-registry`` *Detect*).
"""

from __future__ import annotations

import importlib
import os
import signal
from dataclasses import dataclass

#: Names the armed crash point; paired with the fence below. Both must be set for a
#: kill to fire. A fresh subprocess per armed run, so reading at import is correct.
ENV_CRASH_POINT = "BLIZZARD_CRASH_POINT"
#: The test-scaffolding fence (``bzh:crash-point-registry`` *self-fencing*): the kill
#: fires only when this is ``"1"``, so a stray point name can never crash production.
ENV_CRASH_FENCE = "BLIZZARD_CRASH_FENCE"


@dataclass(frozen=True)
class CrashPoint:
    """A named dangerous window. Declaring one registers it; ``reached()`` is the boundary."""

    name: str
    description: str

    def reached(self) -> None:
        """At this boundary: SIGKILL the process iff this point is armed and fenced.

        Zero meaningful overhead unarmed — one module-global compare and return.
        """
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
    """Every registered crash point, name-sorted — the enumerable list the sweep arms.

    Only points whose declaring module has been imported are present; use
    :func:`discover_crash_points` to force-import the instrumented loop modules first.
    """
    return sorted(_registry.values(), key=lambda p: p.name)


#: The daemon-loop modules that declare crash points at import. Importing them populates
#: the registry — this is the one small, stable list (of *modules*, not point names, which
#: would drift); each module owns its own points at their call sites.
_INSTRUMENTED_MODULES = (
    "blizzard.runner.loop.steps",
    "blizzard.hub.delivery.hub_node",
    "blizzard.hub.domain.claim",
)


def discover_crash_points() -> list[CrashPoint]:
    """Import the instrumented loop modules, then return every registered crash point.

    The sweep calls this to enumerate points programmatically — no hand-maintained
    name list to drift out of sync with the code (``bzh:crash-point-registry``).
    """
    for module in _INSTRUMENTED_MODULES:
        importlib.import_module(module)
    return all_points()


# Read the arming once, at import (a fresh subprocess per armed run — see module docstring).
_ARMED_POINT: str | None = os.environ.get(ENV_CRASH_POINT) or None
_FENCED: bool = os.environ.get(ENV_CRASH_FENCE) == "1"


def _rearm_from_env() -> None:
    """Re-read the arming from the environment — for in-process unit tests of the mechanism only.

    Production and the sweep never call this: every armed run is a fresh subprocess whose
    import-time read is authoritative. A unit test that manipulates ``os.environ`` to prove
    the fence/compare logic calls this to refresh the cached globals.
    """
    global _ARMED_POINT, _FENCED
    _ARMED_POINT = os.environ.get(ENV_CRASH_POINT) or None
    _FENCED = os.environ.get(ENV_CRASH_FENCE) == "1"
