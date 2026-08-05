"""initial empty schema (hub store tree)

The empty baseline of the hub store's tree; fact tables land in later revisions.
Revision ID: 20260713_1112_hub_initial
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "20260713_1112_hub_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
