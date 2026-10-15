"""permits — a project's permits and their append-only facts.

Adds two tenant-keyed tables, permits keyed to their project; downgrade drops them.
Revision ID: 20261015_0900_permits
Revises: 20261014_0900_question_decision_project
"""

# exemplar: bzh:manual-migrations, bzh:frozen-revisions, bzh:sql-portable.

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261015_0900_permits"
down_revision: str | None = "20261014_0900_question_decision_project"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_COLUMN = "tenant_id"
_PERMITS = "permits"
_FACTS = "permit_facts"
#: Indexes this revision adds: name, table, columns.
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_permits_tenant_requested_at_permit_id", _PERMITS, (_TENANT_COLUMN, "requested_at", "permit_id")),
    (
        "ix_permits_tenant_project_requested_at_permit_id",
        _PERMITS,
        (_TENANT_COLUMN, "project_id", "requested_at", "permit_id"),
    ),
    (
        "ix_permits_tenant_holder_requested_at_permit_id",
        _PERMITS,
        (_TENANT_COLUMN, "holder", "requested_at", "permit_id"),
    ),
    ("ix_permit_facts_tenant_permit_id_id", _FACTS, (_TENANT_COLUMN, "permit_id", "id")),
)


def _has_table(bind: sa.Connection, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([_TENANT_COLUMN], ["tenants.tenant_id"], name=f"fk_{table}_tenant_id")


def _create_permits() -> None:
    op.create_table(
        _PERMITS,
        sa.Column("permit_id", sa.String(), nullable=False),
        sa.Column("holder", sa.String(), nullable=False),
        sa.Column("capability", sa.String(), nullable=False),
        sa.Column("requested_at", UtcDateTime(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("permit_id", name="pk_permits"),
        sa.UniqueConstraint(
            _TENANT_COLUMN, "project_id", "holder", "capability", name="uq_permits_tenant_project_holder_capability"
        ),
        _tenant_fk(_PERMITS),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], name="fk_permits_projects"),
    )


def _create_facts() -> None:
    op.create_table(
        _FACTS,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("permit_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("recorded_at", UtcDateTime(), nullable=False),
        sa.Column("recorded_by", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        _tenant_fk(_FACTS),
        sa.ForeignKeyConstraint(["permit_id"], [f"{_PERMITS}.permit_id"], name="fk_permit_facts_permits"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    for table, create in ((_PERMITS, _create_permits), (_FACTS, _create_facts)):
        if not _has_table(bind, table):
            create()
    for name, table, columns in _INDEXES:
        if name not in _index_names(bind, table):
            op.create_index(name, table, list(columns))


def downgrade() -> None:
    bind = op.get_bind()
    for table in (_FACTS, _PERMITS):
        if _has_table(bind, table):
            op.drop_table(table)
