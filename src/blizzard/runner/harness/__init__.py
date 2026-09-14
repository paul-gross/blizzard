"""The harness domain — the coding-harness adapter seam.

Blizzard is coding-harness-agnostic: every harness sits behind one small adapter
(:mod:`.adapter`). Adapters stay **dumb** — they translate, they never decide
(``bzh:deterministic-shell``); reference bindings live under ``internal/``."""

from __future__ import annotations

from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import (
    HarnessBinding,
    HarnessRegistry,
    IHarnessRegistry,
    UnavailableHarnessError,
    UnknownHarnessError,
)

__all__ = [
    "CLAUDE_CODE_HARNESS_ID",
    "HarnessBinding",
    "HarnessRegistry",
    "IHarnessRegistry",
    "SessionReference",
    "UnavailableHarnessError",
    "UnknownHarnessError",
]
