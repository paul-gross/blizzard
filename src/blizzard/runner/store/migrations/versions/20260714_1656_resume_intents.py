"""resume intent fact tables (runner store tree)

The graceful-restart resume marker: ``resume_intents`` (the mark a graceful
``blizzard-runner`` shutdown writes for every active, non-parked, session-bearing lease)
and ``resume_clears`` (the RESUME step's record that it has resumed — or abandoned — the
marked lease). Together they let a runner spin down and back up and re-attach to its own
in-flight Claude sessions in place, instead of retrying each node-step fresh. Before this
the runner had no home for a resume-intent, so every restart discarded in-flight context.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the two new tables, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

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
