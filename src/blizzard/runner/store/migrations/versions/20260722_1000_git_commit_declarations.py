"""worker git-commit declaration channel — git_commit_declarations (runner store tree, issue #143 Phase 3)

The runner's local stash of a worker's explicit git-commit declarations
(``blizzard runner artifact commit --forge <f> --repo <r> --branch <b> --commit <sha>``):
one append-only row per declare call (``id`` PK), latest-wins per ``(lease_id, repo)``.
Authorized by the lease's own capability token (``lease_tokens``) — a structural sibling
of ``20260719_1000_runner_attachments`` for the ``git_commit`` artifact kind. This
revision adds only the storage; no caller yet reads it back (that is Phase 4's ADVANCE
rewrite).

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260722_1000_runner_git_commit_declarations
Revises: 20260721_1500_runner_jwt_jti_seen
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import git_commit_declarations

revision: str = "20260722_1000_runner_git_commit_declarations"
down_revision: str | None = "20260721_1500_runner_jwt_jti_seen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (git_commit_declarations,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
