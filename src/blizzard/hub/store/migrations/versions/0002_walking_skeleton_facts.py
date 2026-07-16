"""walking-skeleton fact tables (hub store tree)

The hub store's first real schema (P6): the fact tables the ingest -> claim ->
commit -> deliver -> land loop derives every chunk status from
(``bzh:facts-not-status``). The tables are defined once in
``blizzard.hub.store.schema`` (the metadata Alembic targets for autogenerate);
this revision creates exactly this revision's subset in FK-dependency order, so a
later revision that adds tables to the same metadata does not get re-created here.

``chunk_pm_pointers`` is the one exception (as of ``0012_pm_pointer_source_ref``):
importing it from ``schema.py`` here would mean this revision's *historical* shape
silently follows whatever ``schema.py`` says today — exactly the bug 0012's own
docstring names and refuses to repeat. This revision instead declares its own frozen
``{provider, url}`` literal for it, so upgrading from ``base`` always recreates the
column shape this revision actually shipped with; 0012 is the one revision that
reshapes it from there.

Revision ID: 0002_hub_walking_skeleton
Revises: 0001_hub_initial
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.hub.store.schema import (
    artifacts,
    chunk_stopped,
    chunks,
    delivery_landed,
    delivery_repo_landed,
    escalations,
    graph_choices,
    graph_edges,
    graph_nodes,
    graphs,
    lease_facts,
    route_created,
    route_environments,
    route_released,
    transitions,
)

revision: str = "0002_hub_walking_skeleton"
down_revision: str | None = "0001_hub_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape — {provider, url} — reshaped by 0012. Not imported
# from schema.py (see the module docstring). No FK object on chunk_id: a separate,
# unregistered MetaData can't resolve a cross-table FK against schema.py's live
# `chunks` (which now carries 0012's shape too) without a name collision on this same
# table; the column is unconstrained here — sqlite doesn't enforce FKs by default —
# exactly as any other pinned-in-time historical migration snapshot would be.
_chunk_pm_pointers = sa.Table(
    "chunk_pm_pointers",
    sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("provider", sa.String, nullable=False),
    sa.Column("url", sa.String, nullable=False),
)

# Parents before children so the FK constraints resolve.
_TABLES = [
    graphs,
    graph_nodes,
    graph_choices,
    graph_edges,
    chunks,
    _chunk_pm_pointers,
    transitions,
    artifacts,
    lease_facts,
    route_created,
    route_environments,
    route_released,
    delivery_repo_landed,
    delivery_landed,
    chunk_stopped,
    escalations,
]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=False)
