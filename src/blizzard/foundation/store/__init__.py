"""Shared store plumbing both daemon trees use.

The portable engine factory (``bzh:sql-portable``) and the Alembic migration
runner + revision-mismatch guard (``bzh:manual-migrations``) live here; each
daemon's own ``store`` package supplies *its* schema and *its* independent
Alembic tree.
"""

from __future__ import annotations
