"""escalation wrapped takeover command (hub store tree, issue #251)

Adds the ``escalations.wrapped_takeover_command`` column: a ``blizzard runner
takeover`` invocation, not the raw harness-resume string ``takeover_command`` already
carries — so the board can render a takeover that actually routes through the runner
rather than bypassing it. The runner's ``_escalate`` composes it and the
runner-reported ``record_escalation`` route persists it from this same change onward
(hub-authored ``record_bounce_escalation`` rows never carry it); see ``schema.py``
for the column's present contract.

The hub store's Alembic tree targets one shared ``schema`` metadata whose table
objects reflect the *current* definition, so a fresh database's 0002 already creates
``escalations`` **with** this column. This revision is therefore written **idempotent**
— it adds the column only where an older database created ``escalations`` without it —
so ``base -> head`` on a fresh store and an in-place upgrade of a pre-#251 store both
land at exactly one column.

Revision ID: 20260803_1000_hub_escalation_wrapped_takeover
Revises: 20260801_1600_hub_runner_external_usage
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_1000_hub_escalation_wrapped_takeover"
down_revision: str | None = "20260801_1600_hub_runner_external_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "escalations"
_COLUMN = "wrapped_takeover_command"


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
