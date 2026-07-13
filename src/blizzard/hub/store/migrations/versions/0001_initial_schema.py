"""initial empty schema (hub store tree)

The initial revision of the hub store's independent Alembic tree (D-099). The
schema is intentionally empty: the hub store is facts-only (``bzh:facts-not-status``),
and its fact tables land in later revisions as the domain model stabilizes. Both
``upgrade`` and ``downgrade`` are real — there is simply nothing to create or drop
at the empty baseline.

Revision ID: 0001_hub_initial
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_hub_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
