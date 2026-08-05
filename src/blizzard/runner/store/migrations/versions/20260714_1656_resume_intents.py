"""resume intent fact tables — ``resume_intents`` (the mark a graceful shutdown writes per
resumable lease) and ``resume_clears`` (resumed or abandoned) (runner store tree)

Revision ID: 20260714_1656_runner_resume_intents
Revises: 20260713_1946_runner_hub_control
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import resume_clears, resume_intents

revision: str = "20260714_1656_runner_resume_intents"
down_revision: str | None = "20260713_1946_runner_hub_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (resume_intents, resume_clears)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
