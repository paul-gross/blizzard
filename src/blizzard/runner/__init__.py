"""``blizzard-runner`` — the supervisor daemon.

The machine-level agent-of-agents (design/runner): a stateless reconciliation
loop (REAP / PULL / FILL / ADVANCE) behind a local API, advancing the chunks it
holds through the hub's workflow graph. Same CLEAN layering as the hub — the
``api`` local edge, a dependency-free ``domain`` core, and a ``store`` with the
runner's own independent Alembic tree.
"""

from __future__ import annotations
