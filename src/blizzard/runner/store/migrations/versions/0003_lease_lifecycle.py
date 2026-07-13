"""lease-lifecycle fact tables (runner store tree)

The walking-skeleton reconciliation loop needs three facts 0002's tables do not
carry, each append-only (``bzh:facts-not-status``): the node context of each lease
attempt (``lease_context``), lease closures (``lease_closures`` — an active lease
is one with no closure), and binding releases (``binding_releases`` — a held env is
one whose binding has no release). Defined once in ``blizzard.runner.store.schema``;
this revision creates exactly its three new tables and touches none of 0002's.

Revision ID: 0003_runner_lease_lifecycle
Revises: 0002_runner_walking_skeleton
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import binding_releases, lease_closures, lease_context

revision: str = "0003_runner_lease_lifecycle"
down_revision: str | None = "0002_runner_walking_skeleton"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [lease_context, lease_closures, binding_releases]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
