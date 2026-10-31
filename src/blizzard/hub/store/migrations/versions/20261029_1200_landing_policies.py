"""landing policies — each repository's landing policy, each project's landing order, and the terms a visit lands by.

Adds `repositories.landing_policy` (default `merge-on-checks`) and `hub_node_poll.kind` (default `pending`), each added
nullable with its default, filled in bounded pages, then tightened to NOT NULL keeping the default; adds nullable
`repositories.park_timeout_hours` and `hub_node_poll.last_polled_at`; creates `project_repository_positions` and
`flow_submission_terms`, empty. Writes no `config_changes` row. Downgrade drops all of it.
Revision ID: 20261029_1200_landing_policies
Revises: 20261029_0900_close_attempt_resume_at
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261029_1200_landing_policies"
down_revision: str | None = "20261029_0900_close_attempt_resume_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_TENANT_COLUMN = "tenant_id"
_REPOSITORIES = "repositories"
_POLL = "hub_node_poll"
_POSITIONS = "project_repository_positions"
_TERMS = "flow_submission_terms"
_POLICY = "landing_policy"
_TIMEOUT = "park_timeout_hours"
_KIND = "kind"
_LAST_POLLED = "last_polled_at"
#: The defaults as this revision writes them — frozen here, never read from the live vocabulary.
_DEFAULT_POLICY = "merge-on-checks"
_DEFAULT_KIND = "pending"

#: The two reshaped tables, frozen to the columns this revision fills: the key it pages by and the column it fills.
_frozen = sa.MetaData()
_repositories = sa.Table(
    _REPOSITORIES,
    _frozen,
    sa.Column("repository_id", sa.String(), primary_key=True),
    sa.Column(_POLICY, sa.String()),
)
_poll = sa.Table(
    _POLL,
    _frozen,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column(_KIND, sa.String()),
)

#: Each tightened column: its table, the frozen table it is filled through, its key, and its default.
_TIGHTENED: tuple[tuple[str, sa.Table, str, str, str], ...] = (
    (_REPOSITORIES, _repositories, "repository_id", _POLICY, _DEFAULT_POLICY),
    (_POLL, _poll, "id", _KIND, _DEFAULT_KIND),
)
#: Each nullable column added beside a tightened one: its table, name, and type.
_NULLABLE: tuple[tuple[str, str, Callable[[], sa.types.TypeEngine[Any]]], ...] = (
    (_REPOSITORIES, _TIMEOUT, sa.Integer),
    (_POLL, _LAST_POLLED, UtcDateTime),
)
#: Indexes this revision adds: name, table, columns.
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "ix_project_repository_positions_tenant_link",
        _POSITIONS,
        (_TENANT_COLUMN, "project_id", "repository_id", "id"),
    ),
    ("ix_flow_submission_terms_tenant_submission_id", _TERMS, (_TENANT_COLUMN, "submission_id")),
)


def _columns(bind: sa.Connection, table: str) -> dict[str, bool]:
    """Each column of ``table`` mapped to whether it is nullable."""
    return {str(c["name"]): bool(c["nullable"]) for c in sa.inspect(bind).get_columns(table)}


def _has_table(bind: sa.Connection, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if _has_table(bind, copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps what its own reflection would drop."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def _add_columns(bind: sa.Connection) -> None:
    """Step 1: each column, nullable, added in place; a tightened one carries its server default from the start."""
    for table, _, _, column, default in _TIGHTENED:
        if column not in _columns(bind, table):
            op.add_column(table, sa.Column(column, sa.String(), nullable=True, server_default=default))
    for table, column, kind in _NULLABLE:
        if column not in _columns(bind, table):
            op.add_column(table, sa.Column(column, kind(), nullable=True))


def _fill(bind: sa.Connection) -> None:
    """Step 2: every row still holding no value takes the default, in bounded pages along the table's key."""
    for _, frozen, key, column, default in _TIGHTENED:
        page = sa.select(frozen.c[key]).where(frozen.c[column].is_(None)).order_by(frozen.c[key]).limit(_BATCH_ROWS)
        while keys := list(bind.execute(page).scalars()):
            bind.execute(sa.update(frozen).where(frozen.c[key].in_(keys)).values({column: default}))


def _tighten(bind: sa.Connection) -> None:
    """Step 3: each filled column becomes required, keeping its default, one table per batch."""
    for table, _, _, column, default in _TIGHTENED:
        if not _columns(bind, table)[column]:
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            batch.alter_column(
                column,
                existing_type=sa.String(),
                existing_server_default=sa.text(f"'{default}'"),
                server_default=default,
                nullable=False,
            )


def _create_positions() -> None:
    op.create_table(
        _POSITIONS,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("repository_id", sa.String(), nullable=False),
        sa.Column("position", sa.Float(), nullable=True),
        sa.Column("set_at", UtcDateTime(), nullable=False),
        sa.Column("set_by", sa.String(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            [_TENANT_COLUMN], ["tenants.tenant_id"], name="fk_project_repository_positions_tenant_id"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "repository_id"],
            ["project_repository_links.project_id", "project_repository_links.repository_id"],
            name="fk_project_repository_positions_links",
        ),
    )


def _create_terms() -> None:
    op.create_table(
        _TERMS,
        sa.Column("submission_id", sa.String(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.Column("repository_id", sa.String(), nullable=False),
        sa.Column(_POLICY, sa.String(), nullable=False),
        sa.Column(_TIMEOUT, sa.Integer(), nullable=True),
        sa.Column("landing_position", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("submission_id", "repository_id", name="pk_flow_submission_terms"),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["flow_submissions.submission_id"], name="fk_flow_submission_terms_submissions"
        ),
        sa.ForeignKeyConstraint([_TENANT_COLUMN], ["tenants.tenant_id"], name="fk_flow_submission_terms_tenant_id"),
    )


#: Each table this revision creates, and its creator.
_TABLES = ((_POSITIONS, _create_positions), (_TERMS, _create_terms))


def _create_tables(bind: sa.Connection) -> None:
    """Step 4: the two new tables and their indexes, each skipped when present."""
    for table, create in _TABLES:
        if not _has_table(bind, table):
            create()
    for name, table, columns in _INDEXES:
        if name not in _index_names(bind, table):
            op.create_index(name, table, list(columns))


#: The upgrade's steps, in order; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS: tuple[Callable[[sa.Connection], None], ...] = (_add_columns, _fill, _tighten, _create_tables)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table, _ in reversed(_TABLES):
        if _has_table(bind, table):
            op.drop_table(table)
    for table in (_POLL, _REPOSITORIES):
        added = {column for t, _, _, column, _ in _TIGHTENED if t == table} | {
            column for t, column, _ in _NULLABLE if t == table
        }
        held = sorted(added & set(_columns(bind, table)))
        if not held:
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            for column in held:
                batch.drop_column(column)
