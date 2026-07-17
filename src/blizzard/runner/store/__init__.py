"""The runner store — machine-local execution facts, and the runner's Alembic tree.

Facts only; status is always derived (``bzh:facts-not-status``): leases,
heartbeats, pids, env bindings, epochs — the machine's execution right now,
sqlite embedded in the daemon. The migration tree under
``migrations/`` is **independent** of the hub's. Shared plumbing lives in
``blizzard.foundation.store``; this package supplies the runner's schema metadata
and the location of its tree.
"""

from __future__ import annotations

from pathlib import Path

# The runner's own Alembic tree — an independent migration line.
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# The store name used in revision-mismatch messages.
STORE_NAME = "runner"
