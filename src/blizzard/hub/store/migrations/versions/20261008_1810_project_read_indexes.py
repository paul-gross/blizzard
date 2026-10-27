"""project read indexes — the tenant-led garden indexes beside the project-led ones go.

Drops the five ``(tenant_id, …)`` finding, finding-set and proposal indexes 20261008_1800_projects kept beside their
``(tenant_id, project_id, …)`` counterparts.
Each index is inspected first, so a re-run changes nothing.

The downgrade restores the five indexes.
Revision ID: 20261008_1810_project_read_indexes
Revises: 20261008_1800_projects
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_1810_project_read_indexes"
down_revision: str | None = "20261008_1800_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The tenant-led garden indexes this revision drops: name, table, columns.
DROPPED_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_findings_tenant_routine_scope", "findings", ("tenant_id", "routine_name", "scope_slug")),
    ("ix_findings_tenant_routine_class", "findings", ("tenant_id", "routine_name", "class")),
    ("ix_findings_tenant_scope_source", "findings", ("tenant_id", "scope_slug", "source")),
    ("ix_finding_sets_tenant_routine_scope", "finding_sets", ("tenant_id", "routine_name", "scope_slug")),
    ("ix_garden_proposals_tenant_routine_class", "garden_proposals", ("tenant_id", "routine_name", "class")),
)


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for name, table, _columns in DROPPED_INDEXES:
        if name in _index_names(bind, table):
            op.drop_index(name, table_name=table)


def downgrade() -> None:
    bind = op.get_bind()
    for name, table, columns in DROPPED_INDEXES:
        if name not in _index_names(bind, table):
            op.create_index(name, table, list(columns))
