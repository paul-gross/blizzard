"""An index on ``lease_closures (lease_id, id)`` for the trace drain's first-closure-per-lease read (runner store tree).

Revision ID: 20261009_1000_runner_lease_closures_lease_id_index
Revises: 20261008_1100_runner_hub_projects
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261009_1000_runner_lease_closures_lease_id_index"
down_revision: str | None = "20261008_1100_runner_hub_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "lease_closures"
_INDEX = "ix_lease_closures_lease_id_id"


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {str(i["name"]) for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, ["lease_id", "id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
