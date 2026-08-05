"""workspace prompt override table (runner store tree)

The override only (issue #17) — the static source stays in config — one row per workspace.
Revision ID: 20260715_1633_runner_workspace_prompt
Revises: 20260714_1656_runner_resume_intents
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import workspace_prompt

revision: str = "20260715_1633_runner_workspace_prompt"
down_revision: str | None = "20260714_1656_runner_resume_intents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    workspace_prompt.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    workspace_prompt.drop(op.get_bind(), checkfirst=True)
