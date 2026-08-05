"""session-end signal fact table (runner store tree) — ``session_ends`` records the
durable "the worker declared done" fact whose *absence* marks a crash (issue #13).

Revision ID: 20260715_1641_runner_session_ends
Revises: 20260715_1633_runner_workspace_prompt
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import session_ends

revision: str = "20260715_1641_runner_session_ends"
down_revision: str | None = "20260715_1633_runner_workspace_prompt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    session_ends.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    session_ends.drop(op.get_bind(), checkfirst=True)
