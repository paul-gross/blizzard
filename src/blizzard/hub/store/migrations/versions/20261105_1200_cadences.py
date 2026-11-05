"""cadences — a project's rhythm and the chores sharing it.

Creates `cadences`, `cadence_chores`, `cadence_chore_scopes`, and `cadence_lifecycle_facts`, empty, each tenant-keyed
with a tenant-led index. Touches no existing table and writes no `config_changes` row. Downgrade drops the four.
Revision ID: 20261105_1200_cadences
Revises: 20261105_0900_routine_slug
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261105_1200_cadences"
down_revision: str | None = "20261105_0900_routine_slug"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT = "tenant_id"
_CADENCES = "cadences"
_CHORES = "cadence_chores"
_CHORE_SCOPES = "cadence_chore_scopes"
_FACTS = "cadence_lifecycle_facts"

#: Indexes this revision adds: name, table, columns.
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_cadences_tenant_project_slug", _CADENCES, (_TENANT, "project_id", "slug")),
    ("ix_cadence_chores_tenant_cadence_id", _CHORES, (_TENANT, "cadence_id")),
    ("ix_cadence_chore_scopes_tenant_chore_id", _CHORE_SCOPES, (_TENANT, "chore_id")),
    ("ix_cadence_lifecycle_facts_tenant_cadence_id", _FACTS, (_TENANT, "cadence_id")),
)


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([_TENANT], ["tenants.tenant_id"], name=f"fk_{table}_tenant_id")


def _create_cadences() -> None:
    op.create_table(
        _CADENCES,
        sa.Column("cadence_id", sa.String(), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("schedule_cron", sa.String(), nullable=True),
        sa.Column("schedule_zone", sa.String(), nullable=True),
        sa.Column("spacing_minutes", sa.Integer(), nullable=True),
        sa.Column("anchor", sa.String(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(_TENANT, sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        _tenant_fk(_CADENCES),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"], name="fk_cadences_projects"),
        sa.UniqueConstraint("project_id", "slug", name="uq_cadences_project_slug"),
    )


def _create_chores() -> None:
    op.create_table(
        _CHORES,
        sa.Column("chore_id", sa.String(), primary_key=True),
        sa.Column("cadence_id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("routine_slug", sa.String(), nullable=False),
        sa.Column("scope_selection", sa.String(), nullable=False),
        sa.Column("scopes_set_at", UtcDateTime(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("promote", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column(_TENANT, sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["cadence_id"], ["cadences.cadence_id"], name="fk_cadence_chores_cadences"),
        _tenant_fk(_CHORES),
        sa.UniqueConstraint("cadence_id", "key", name="uq_cadence_chores_cadence_key"),
    )


def _create_chore_scopes() -> None:
    op.create_table(
        _CHORE_SCOPES,
        sa.Column("chore_id", sa.String(), nullable=False),
        sa.Column("scope_slug", sa.String(), nullable=False),
        sa.Column(_TENANT, sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["chore_id"], ["cadence_chores.chore_id"], name="fk_cadence_chore_scopes_chores"),
        _tenant_fk(_CHORE_SCOPES),
        sa.PrimaryKeyConstraint("chore_id", "scope_slug", name="pk_cadence_chore_scopes"),
    )


def _create_facts() -> None:
    op.create_table(
        _FACTS,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cadence_id", sa.String(), nullable=False),
        sa.Column("retired", sa.Boolean(), nullable=False),
        sa.Column("set_at", UtcDateTime(), nullable=False),
        sa.Column("set_by", sa.String(), nullable=False),
        sa.Column(_TENANT, sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["cadence_id"], ["cadences.cadence_id"], name="fk_cadence_lifecycle_facts_cadences"),
        _tenant_fk(_FACTS),
    )


#: Each table this revision creates, parents first, and its creator.
_TABLES: tuple[tuple[str, Callable[[], None]], ...] = (
    (_CADENCES, _create_cadences),
    (_CHORES, _create_chores),
    (_CHORE_SCOPES, _create_chore_scopes),
    (_FACTS, _create_facts),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table, create in _TABLES:
        if not sa.inspect(bind).has_table(table):
            create()
    for name, table, columns in _INDEXES:
        if name not in {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}:
            op.create_index(name, table, list(columns))


def downgrade() -> None:
    bind = op.get_bind()
    for table, _ in reversed(_TABLES):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
