"""served project color — a nullable ``hub_served_projects.color``: the palette color of the project each answered
entry resolves to, a palette name or a ``#rrggbb``. Existing rows take NULL until the hub next answers; no other table
changes, and a rerun skips it. ``downgrade()`` drops the column: the next answer restores it.

Revision ID: 20261019_1000_served_project_color
Revises: 20261015_1000_host_ingests
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261019_1000_served_project_color"
down_revision: str | None = "20261015_1000_host_ingests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "hub_served_projects"
_COLUMN = "color"


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    if _COLUMN not in _columns(op.get_bind()):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    if _COLUMN in _columns(op.get_bind()):
        op.drop_column(_TABLE, _COLUMN)
