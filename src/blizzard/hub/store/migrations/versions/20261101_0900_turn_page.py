"""Bound submission turn pages by tenant, submission and turn ID.

Revision ID: 20261101_0900_turn_page
Revises: 20261030_0900_park_wait
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20261101_0900_turn_page"
down_revision: str | None = "20261030_0900_park_wait"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_hub_turns_tenant_submission_turn"


def upgrade() -> None:
    op.create_index(_INDEX, "hub_turns", ["tenant_id", "submission_id", "turn_id"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="hub_turns")
