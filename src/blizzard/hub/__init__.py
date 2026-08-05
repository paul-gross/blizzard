"""``blizzard-hub`` — the work orchestrator daemon.

The fleet's shared memory and the human's front door: work-source binding, the chunk
queue, the workflow record, artifacts, asks, and the merge queue, over HTTP + SSE.
CLEAN-layered: an ``api`` edge, a dependency-free ``domain``, its own Alembic ``store``.
"""

from __future__ import annotations
