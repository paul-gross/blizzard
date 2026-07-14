"""open-PR delivery facts — pr.opened / pr.closed (hub store tree)

The ``open-pr`` deliver-mode facts (design/workflow-engine.md, D-059/D-065):

* ``delivery_pr_opened`` — the coordinator's park record: a PR was opened for a repo's
  branch, no terminal transition written, environments held (D-066).
* ``delivery_pr_closed`` — the terminal fact a poll or ``POST /chunks/{id}/check-delivery``
  writes when the PR reaches a terminal state; ``merged`` distinguishes the two dispositions
  and ``landed_commit`` carries the merge commit where one exists (D-065).

Revision ID: 0010_hub_delivery_pr_facts
Revises: 0009_hub_runner_registry
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import delivery_pr_closed, delivery_pr_opened

revision: str = "0010_hub_delivery_pr_facts"
down_revision: str | None = "0009_hub_runner_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [delivery_pr_opened, delivery_pr_closed]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
