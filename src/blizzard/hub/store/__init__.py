"""The hub store — the fleet facts, and the hub's own Alembic tree.

Facts only; status is always derived (``bzh:facts-not-status``). The migration
tree under ``migrations/`` is **independent** of the runner's: its own
revision line, its own lifecycle. The shared plumbing (engine, migration runner,
revision guard) lives in ``blizzard.foundation.store``; this package supplies the
hub's schema metadata and the location of its tree.
"""

from __future__ import annotations

from pathlib import Path

# The hub's own Alembic tree — an independent migration line.
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# The store name used in revision-mismatch messages.
STORE_NAME = "hub"
