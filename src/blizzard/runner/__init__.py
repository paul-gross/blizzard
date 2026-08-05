"""``blizzard-runner`` — the supervisor daemon.

The machine-level agent-of-agents: a stateless reconciliation loop (REAP / PULL /
FILL / ADVANCE) behind a local API, advancing the chunks it holds. CLEAN layering —
an ``api`` local edge, a dependency-free ``domain`` core, and its own ``store``."""

from __future__ import annotations
