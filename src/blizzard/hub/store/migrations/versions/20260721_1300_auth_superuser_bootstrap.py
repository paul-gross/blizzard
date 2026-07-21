"""superuser_bootstrap — the ``auth.superuser`` bootstrap lifecycle (issue #94, hub store tree)

The singleton row ``AuthService``'s bootstrap methods (``hub/auth/bootstrap.py``) read
and rewrite: which email is the currently configured ``auth.superuser`` target, and —
once known — which user claimed it. Carries a foreign key onto ``users`` (from #91),
so this revision comes after the identity spine.

``table.create(bind, checkfirst=True)`` mirrors the two prior auth revisions'
own idempotent single-table create.

Revision ID: 20260721_1300_hub_auth_superuser_bootstrap
Revises: 20260721_1200_hub_auth_oauth
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import superuser_bootstrap

revision: str = "20260721_1300_hub_auth_superuser_bootstrap"
down_revision: str | None = "20260721_1200_hub_auth_oauth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    superuser_bootstrap.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    superuser_bootstrap.drop(op.get_bind(), checkfirst=True)
