"""``blizzard-hub`` — the work orchestrator daemon.

The fleet's shared memory and the human's front door (design/hub): PM binding,
the chunk queue, the workflow record, artifacts, asks, and the merge queue,
over an HTTP API + SSE. Structured by the CLEAN layering the harness carries
over: the ``api`` HTTP edge, a dependency-free ``domain`` core, and a ``store``
with the hub's own independent Alembic tree.
"""

from __future__ import annotations
