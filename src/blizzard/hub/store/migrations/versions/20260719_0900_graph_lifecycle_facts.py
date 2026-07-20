"""graph lifecycle facts — a reversible retire/re-enable brake over one specific
graph_id (hub store tree)

Issue #101 gives an operator a retire/re-enable lever over a minted graph, keyed on
its ``graph_id``. ``graph_lifecycle_facts`` mirrors ``chunk_pause_facts`` exactly:
append-only, newest-fact-wins. The ``graphs`` table itself is untouched — it stays
insert-only and immutable; only name resolution (``get_enabled_by_name``/
``mark_effective``) excludes a retired ``graph_id`` from its candidate set.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260719_0900_hub_graph_lifecycle_facts
Revises: 20260718_1300_hub_runner_env_capacity
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import graph_lifecycle_facts

revision: str = "20260719_0900_hub_graph_lifecycle_facts"
down_revision: str | None = "20260718_1300_hub_runner_env_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (graph_lifecycle_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
