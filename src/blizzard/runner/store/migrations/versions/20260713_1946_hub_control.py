"""hub control mirror — the declarative pause brake read on PULL (runner store tree)

P7W3 lands the runner's local mirror of the hub's declarative pause brake (D-043/D-012):
``hub_control`` holds the last ``paused`` value PULL read back from the hub registry, so
FILL adheres (no new claims while paused; in-flight chunks run on) without calling the
hub itself and keeps the last-known directive while the hub is unreachable.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260713_1946_runner_hub_control
Revises: 20260713_1801_runner_asks_and_parks
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import hub_control

revision: str = "20260713_1946_runner_hub_control"
down_revision: str | None = "20260713_1801_runner_asks_and_parks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (hub_control,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
