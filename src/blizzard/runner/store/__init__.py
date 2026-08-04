"""The runner store — machine-local execution facts, and the runner's Alembic tree.

Facts only; status is always derived (``bzh:facts-not-status``): leases,
heartbeats, pids, env bindings, epochs. The migration tree under ``migrations/``
is **independent** of the hub's. Shared plumbing: ``blizzard.foundation.store``.
"""

from __future__ import annotations

from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# The store name used in revision-mismatch messages.
STORE_NAME = "runner"
