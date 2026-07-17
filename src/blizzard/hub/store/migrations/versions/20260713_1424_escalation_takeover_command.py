"""escalation takeover command (hub store tree)

Adds the ``escalations.takeover_command`` column (P7): the runner-composed, pasteable
``cd <workdir> && <harness resume>`` a human runs to enter a parked ``needs_human``
session (design/harness-adapters.md, D-035). The escalation fact already existed
(0002); this revision carries the resume command alongside it so the hub can surface
an actionable takeover on the board.

The hub store's Alembic tree targets one shared ``schema`` metadata whose table
objects reflect the *current* definition, so a fresh database's 0002 already creates
``escalations`` **with** this column. This revision is therefore written **idempotent**
— it adds the column only where an older database created ``escalations`` without it —
so ``base -> head`` on a fresh store and an in-place upgrade of a pre-P7 store both
land at exactly one column.

Revision ID: 20260713_1424_hub_escalation_takeover
Revises: 20260713_1218_hub_walking_skeleton
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_1424_hub_escalation_takeover"
down_revision: str | None = "20260713_1218_hub_walking_skeleton"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "escalations"
_COLUMN = "takeover_command"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
