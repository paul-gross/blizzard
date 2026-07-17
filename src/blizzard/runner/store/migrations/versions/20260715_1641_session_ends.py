"""session-end signal fact table (runner store tree)

The ungraceful-restart counterpart to ``resume_intents`` (issue #13):
``session_ends`` records the durable "the worker declared done" fact the Claude Code
``SessionEnd`` hook posts on a natural session exit. A ``kill -9`` / OOM / reboot never
runs that hook, so a killed-mid-work lease has no row — and startup crash-recovery reads
that *absence* (paired with a dead pid) to tell a crash it must resume from a clean exit
ADVANCE should judge. Before this the runner had no home for the signal, so an involuntary
restart discarded every in-flight session's context.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

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
