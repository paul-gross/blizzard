"""worker attachment channel — attachments (runner store tree, issue #113 Phase 2)

The runner's local stash of a worker's explicit ``produces:`` submissions
(``blizzard runner attach --name <n>``): one append-only row per attach call
(``id`` PK), latest-wins per ``(lease_id, name)``. Authorized by the lease's own
capability token (``lease_tokens``, Phase 1) — this revision adds only the
storage; no caller yet reads it back to prefer it over the judgement assessment
(Phase 3).

Each revision in this tree creates a subset of the current ``schema`` metadata's
tables (the live-schema pattern); this one creates exactly the one new table,
``checkfirst`` so a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260719_1000_runner_attachments
Revises: 20260719_0900_runner_lease_tokens
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import attachments

revision: str = "20260719_1000_runner_attachments"
down_revision: str | None = "20260719_0900_runner_lease_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (attachments,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
