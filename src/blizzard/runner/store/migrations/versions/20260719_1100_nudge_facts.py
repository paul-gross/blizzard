"""runner-side nudge-once fact — nudge_facts (runner store tree, issue #113 Phase 4)

The durable guard ``_advance_exited_worker`` (``runner/loop/steps.py``) consults
before ever resuming a worker session to nudge it about an unattached ``produces:``
name: at most one row per ``(lease_id, epoch)``. Written before the resume it
guards, so "at most one nudge per (lease, epoch)" holds structurally across a crash
either at the write or at the resume that follows it.

Each revision in this tree creates a subset of the current ``schema`` metadata's
tables (the live-schema pattern); this one creates exactly the one new table,
``checkfirst`` so a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260719_1100_runner_nudge_facts
Revises: 20260719_1000_runner_attachments
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import nudge_facts

revision: str = "20260719_1100_runner_nudge_facts"
down_revision: str | None = "20260719_1000_runner_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (nudge_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
