"""workspace prompt override table (runner store tree)

The runtime-settable spawn preamble (issue #17): ``workspace_prompt`` holds the
runner-owned workspace prompt an operator replaces at runtime through the local API
(``PUT /api/workspace-prompt``), so a new prompt applies to subsequent worker spawns
with no daemon restart. Its static source stays in config (``blizzard-runner.toml``);
this table is only the override, upserted one row per workspace. Before this the
runner had no home for a runtime prompt, so the spawn preamble could not change
without a restart.

Each revision in this tree creates a subset of the current ``schema`` metadata's
tables (the live-schema pattern); this one creates exactly the new table,
``checkfirst`` so a fresh ``base -> head`` and an in-place upgrade both converge.

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
