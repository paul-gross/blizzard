"""Crash-point discovery, test-side (``bzh:crash-point-registry``).

No operator-facing surface enumerates crash points — not the CLI tree, not
``contracts/cli/``, not any HTTP route — so the instrumented-module roster and the
import-and-enumerate step that walks it belong to the tests that are its only callers,
not to the production registry itself."""

from __future__ import annotations

import importlib

from blizzard.foundation.crash import CrashPoint

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


def discover_crash_points() -> list[CrashPoint]:
    """Import the instrumented modules, then return every registered crash point."""
    for module in _INSTRUMENTED_MODULES:
        importlib.import_module(module)
    return CrashPoint.all()
