"""The harness domain — the coding-harness adapter seam.

Blizzard is coding-harness-agnostic: every harness sits behind one small adapter (:mod:`.adapter`).
Adapters stay **dumb** — they translate, they never decide (``bzh:deterministic-shell``). The reference
bindings are the adapter packages :mod:`.claude_code` and :mod:`.opencode`; within ``harness/`` only
:mod:`.wiring` names them."""

from __future__ import annotations

from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import (
    HarnessBinding,
    HarnessRegistry,
    IHarnessRegistry,
    UnavailableHarnessError,
    UnknownHarnessError,
)

__all__ = [
    "HarnessBinding",
    "HarnessRegistry",
    "IHarnessRegistry",
    "SessionReference",
    "UnavailableHarnessError",
    "UnknownHarnessError",
]
