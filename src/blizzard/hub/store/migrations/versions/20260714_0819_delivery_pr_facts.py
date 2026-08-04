"""open-PR delivery facts — pr.opened / pr.closed (hub store tree)

The ``open-pr`` deliver-mode facts:

* ``delivery_pr_opened`` — the coordinator's park record: a PR was opened for a repo's
  branch, no terminal transition written, environments held.
* ``delivery_pr_closed`` — the terminal fact a poll or ``POST /chunks/{id}/check-delivery``
  writes when the PR reaches a terminal state; ``merged`` distinguishes the two dispositions
  and ``landed_commit`` carries the merge commit where one exists.

``delivery_pr_opened`` is the one exception: it is a frozen local literal — no
``uq_delivery_pr_opened_chunk_repo`` — rather than a ``schema.py`` import, so upgrading
from ``base`` recreates the shape this revision shipped with and ``0014`` stays the one
revision that adds the constraint (``canon:no-retro``; pinned by
``tests/test_pin_hub_api.py::test_delivery_pr_opened_gains_its_uniqueness_only_at_the_revision_that_adds_it``).
``delivery_pr_closed`` is untouched by any later revision, so it is still safely imported
from ``schema.py``.

Revision ID: 20260714_0819_hub_delivery_pr_facts
Revises: 20260713_1947_hub_runner_registry
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.hub.store.schema import delivery_pr_closed

revision: str = "20260714_0819_hub_delivery_pr_facts"
down_revision: str | None = "20260713_1947_hub_runner_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — no (chunk_id, repo) uniqueness — reshaped by 0014.
# Not imported from schema.py (see the module docstring). A bare
# `sa.ForeignKey("chunks.chunk_id")` needs a `chunks` table it can resolve against in the
# *same* MetaData; this is purely a resolution stub (mirroring 0002's), never added to
# `_TABLES` and never created or dropped — `chunks` itself was created by 0002.
_frozen_metadata = sa.MetaData()
sa.Table(
    "chunks",
    _frozen_metadata,
    sa.Column("chunk_id", sa.String, primary_key=True),
)
_delivery_pr_opened = sa.Table(
    "delivery_pr_opened",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, sa.ForeignKey("chunks.chunk_id"), nullable=False),
    sa.Column("repo", sa.String, nullable=False),
    sa.Column("pr_number", sa.Integer, nullable=False),
    sa.Column("pr_url", sa.String, nullable=False),
    sa.Column("commit_hash", sa.String, nullable=False),
    sa.Column("opened_at", sa.DateTime, nullable=False),
)

_TABLES = [_delivery_pr_opened, delivery_pr_closed]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
