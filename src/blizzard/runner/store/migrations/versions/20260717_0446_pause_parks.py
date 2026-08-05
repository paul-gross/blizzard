"""pause parks (issue #46) — a table pair separate from ``park_facts``/``park_resumes``,
since a nullable ``question_id`` there would make ``NOT IN (... NULL)`` swallow every row

Revision ID: 20260717_0446_runner_pause_parks
Revises: 20260716_1511_runner_local_pause
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import pause_park_resumes, pause_parks

revision: str = "20260717_0446_runner_pause_parks"
down_revision: str | None = "20260716_1511_runner_local_pause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (pause_parks, pause_park_resumes)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
