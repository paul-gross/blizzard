"""runner-side check results + checks-ran guard — check_results, checks_ran (runner store tree, issue #114)

``checks_ran`` is written AFTER the ``check_results`` rows: unset on recovery ⇒ re-run all.
Revision ID: 20260725_1200_runner_check_results
Revises: 20260722_1000_runner_git_commit_declarations
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import check_results, checks_ran

revision: str = "20260725_1200_runner_check_results"
down_revision: str | None = "20260722_1000_runner_git_commit_declarations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (check_results, checks_ran)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
