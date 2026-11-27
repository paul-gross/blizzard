"""Add the optional transition_reason recorded with a transition."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261117_0900_transition_reason"
down_revision: str | None = "20261116_0900_egress_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "reason" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("transitions")}:
        op.add_column("transitions", sa.Column("reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transitions") as batch:
        batch.drop_column("reason")
